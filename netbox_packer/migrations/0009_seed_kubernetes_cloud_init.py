import hashlib

from django.db import migrations

KUBERNETES_CLOUD_CONFIG = """#cloud-config
# Kubernetes 1.31 node image on Ubuntu 24.04.
# Applied to cloned VMs via Proxmox cicustom user-data at first boot.
# The resulting template is a pre-staged Kubernetes node: containerd runtime,
# kubelet/kubeadm/kubectl v1.31 from the official Kubernetes APT repo, kernel
# modules, and sysctl tuning.  Run kubeadm init/join after first boot.
package_update: true
package_upgrade: true
packages:
  - ca-certificates
  - curl
  - gnupg
  - apt-transport-https
  - qemu-guest-agent
write_files:
  - path: /etc/modules-load.d/k8s.conf
    permissions: "0644"
    owner: root:root
    content: |
      overlay
      br_netfilter
  - path: /etc/sysctl.d/99-k8s.conf
    permissions: "0644"
    owner: root:root
    content: |
      net.bridge.bridge-nf-call-iptables  = 1
      net.bridge.bridge-nf-call-ip6tables = 1
      net.ipv4.ip_forward                 = 1
  - path: /opt/k8s-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive

      # --- kernel modules ---
      modprobe overlay
      modprobe br_netfilter
      sysctl --system

      # --- disable swap permanently ---
      swapoff -a
      sed -i '/\\bswap\\b/d' /etc/fstab
      systemctl mask swap.target 2>/dev/null || true

      # --- containerd ---
      install -d -m 0755 /etc/apt/keyrings
      curl --fail --silent --location \\
        https://download.docker.com/linux/ubuntu/gpg \\
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \\
        https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \\
        > /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y containerd.io
      install -d -m 0755 /etc/containerd
      containerd config default > /etc/containerd/config.toml
      sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
      systemctl enable --now containerd

      # --- crictl points at containerd ---
      cat > /etc/crictl.yaml <<'CRICTL'
      runtime-endpoint: unix:///run/containerd/containerd.sock
      image-endpoint: unix:///run/containerd/containerd.sock
      timeout: 30
      debug: false
      CRICTL

      # --- kubernetes 1.31 APT repo ---
      curl --fail --silent --location \\
        https://pkgs.k8s.io/core:/stable:/v1.31/deb/Release.key \\
        | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
      chmod a+r /etc/apt/keyrings/kubernetes-apt-keyring.gpg
      echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] \\
        https://pkgs.k8s.io/core:/stable:/v1.31/deb/ /" \\
        > /etc/apt/sources.list.d/kubernetes.list
      apt-get update
      apt-get install -y kubelet=1.31.* kubeadm=1.31.* kubectl=1.31.*
      apt-mark hold kubelet kubeadm kubectl
      systemctl enable kubelet

      # --- pre-pull control-plane images for fast kubeadm init ---
      kubeadm config images pull --kubernetes-version 1.31.0

      # --- qemu guest agent (already in packages list; ensure it starts) ---
      systemctl enable --now qemu-guest-agent

      echo "k8s-bootstrap complete"
runcmd:
  - [bash, /opt/k8s-bootstrap.sh]
"""

CONFIG_NAME = "k8s-1.31-ubuntu-2404-node"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "k8s-1.31-ubuntu-2404-node"
# Production Proxmox endpoint.  Do NOT redirect this build to 10.0.30.139.
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
TEMPLATE_VMID = 9012


def seed_kubernetes(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": KUBERNETES_CLOUD_CONFIG,
            "checksum": hashlib.sha256(KUBERNETES_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "Kubernetes 1.31 node image on Ubuntu 24.04. Installs containerd, "
                "kubelet, kubeadm, and kubectl; pre-pulls control-plane images; "
                "configures kernel modules and sysctl. Baked as a Proxmox cicustom "
                "user snippet via proxbox-api on endpoint 10.0.30.71."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=TEMPLATE_NAME,
        defaults={
            "os_family": "ubuntu",
            "os_version": "24.04",
            "proxmox_template_id": TEMPLATE_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": "10.0.30.71",
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": config,
            "description": (
                "Kubernetes 1.31 cloud-init template image (Ubuntu 24.04). Builds via "
                "proxbox-api on ProxmoxEndpoint 10.0.30.71 using storage 'local'. "
                "Produces a ready-to-clone node template; run kubeadm init/join "
                "after first boot."
            ),
        },
    )


def unseed_kubernetes(apps, schema_editor):
    # Intentionally a no-op: do not delete operator data on reverse migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0008_packertemplate_monitoring_agents"),
    ]

    operations = [
        migrations.RunPython(seed_kubernetes, unseed_kubernetes),
    ]
