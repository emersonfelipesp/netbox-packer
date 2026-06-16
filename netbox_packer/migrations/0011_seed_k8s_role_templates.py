import hashlib

from django.db import migrations

# ---------------------------------------------------------------------------
# Control Plane cloud-config
# Installs containerd + kubelet/kubeadm/kubectl and pre-pulls the
# Kubernetes 1.31 control-plane images so that kubeadm init completes
# offline after first-boot provisioning.
# ---------------------------------------------------------------------------
K8S_CONTROL_PLANE_CONFIG = """#cloud-config
# Kubernetes 1.31 Control Plane image on Ubuntu 24.04.
# Applied to cloned VMs via Proxmox cicustom user-data at first boot.
# Installs the container runtime and Kubernetes binaries, then pre-pulls
# control-plane component images (etcd, apiserver, controller-manager,
# scheduler, coredns, pause) so that kubeadm init runs fast after boot.
# Does NOT run kubeadm init — the nms-backend provisioning engine does
# that step over SSH after the VM starts.
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
  - path: /opt/k8s-cp-bootstrap.sh
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
      # etcd, kube-apiserver, kube-controller-manager, kube-scheduler,
      # coredns, pause — these are only needed on the control plane.
      kubeadm config images pull --kubernetes-version 1.31.0

      # --- qemu guest agent ---
      systemctl enable --now qemu-guest-agent

      echo "k8s-control-plane-bootstrap complete"
runcmd:
  - [bash, /opt/k8s-cp-bootstrap.sh]
"""

# ---------------------------------------------------------------------------
# Worker Node cloud-config
# Same container runtime + binaries as the CP, but skips the control-plane
# image pre-pull (etcd, apiserver, etc. are not needed on workers).
# Workers only run the pause image, kubelet, and application pods.
# ---------------------------------------------------------------------------
K8S_WORKER_NODE_CONFIG = """#cloud-config
# Kubernetes 1.31 Worker Node image on Ubuntu 24.04.
# Applied to cloned VMs via Proxmox cicustom user-data at first boot.
# Installs the container runtime and Kubernetes binaries.
# Does NOT pre-pull control-plane images (only needed on the CP).
# Does NOT run kubeadm join — the nms-backend provisioning engine does
# that step over SSH after the VM starts.
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
  - path: /opt/k8s-worker-bootstrap.sh
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

      # --- qemu guest agent ---
      systemctl enable --now qemu-guest-agent

      echo "k8s-worker-node-bootstrap complete"
runcmd:
  - [bash, /opt/k8s-worker-bootstrap.sh]
"""

# Template metadata
CP_CONFIG_NAME = "k8s-1.31-control-plane-cloud-config"
CP_TEMPLATE_NAME = "k8s-1.31-control-plane-ubuntu-2404"
CP_VMID = 9013

WORKER_CONFIG_NAME = "k8s-1.31-worker-node-cloud-config"
WORKER_TEMPLATE_NAME = "k8s-1.31-worker-node-ubuntu-2404"
WORKER_VMID = 9014

CONFIG_VERSION = "1.0.0"
# Production Proxmox endpoint. Do NOT redirect to the development host.
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"


def seed_k8s_role_templates(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    # --- Control Plane ---
    cp_config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CP_CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": K8S_CONTROL_PLANE_CONFIG,
            "checksum": hashlib.sha256(K8S_CONTROL_PLANE_CONFIG.encode()).hexdigest(),
            "description": (
                "Kubernetes 1.31 Control Plane cloud-config on Ubuntu 24.04. "
                "Installs containerd, kubelet, kubeadm, kubectl, and pre-pulls "
                "all control-plane images (etcd, apiserver, controller-manager, "
                "scheduler, coredns, pause) via 'kubeadm config images pull'. "
                "Does not run kubeadm init — that is handled by nms-backend "
                "provisioning over SSH after the VM starts."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=CP_TEMPLATE_NAME,
        defaults={
            "os_family": "ubuntu",
            "os_version": "24.04",
            "proxmox_template_id": CP_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": PROXMOX_NODE,
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": cp_config,
            "description": (
                "Kubernetes 1.31 Control Plane template (Ubuntu 24.04, VMID 9013). "
                "Baked via proxbox-api on ProxmoxEndpoint 10.0.30.71 using storage "
                "'local'. Clone this template for CP VMs; kubeadm init is run by "
                "the nms-backend K8s provisioning engine after first boot."
            ),
        },
    )

    # --- Worker Node ---
    worker_config, _ = PackerInstallerConfig.objects.get_or_create(
        name=WORKER_CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": K8S_WORKER_NODE_CONFIG,
            "checksum": hashlib.sha256(K8S_WORKER_NODE_CONFIG.encode()).hexdigest(),
            "description": (
                "Kubernetes 1.31 Worker Node cloud-config on Ubuntu 24.04. "
                "Installs containerd, kubelet, kubeadm, and kubectl. Does not "
                "pre-pull control-plane images (not needed on workers). Does not "
                "run kubeadm join — that is handled by nms-backend provisioning "
                "over SSH after the VM starts."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=WORKER_TEMPLATE_NAME,
        defaults={
            "os_family": "ubuntu",
            "os_version": "24.04",
            "proxmox_template_id": WORKER_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": PROXMOX_NODE,
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": worker_config,
            "description": (
                "Kubernetes 1.31 Worker Node template (Ubuntu 24.04, VMID 9014). "
                "Baked via proxbox-api on ProxmoxEndpoint 10.0.30.71 using storage "
                "'local'. Clone this template for worker VMs; kubeadm join is run "
                "by the nms-backend K8s provisioning engine after first boot."
            ),
        },
    )


def unseed_k8s_role_templates(apps, schema_editor):
    # Intentionally a no-op: do not delete operator data on reverse migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0010_alter_packertemplate_zabbix_server"),
    ]

    operations = [
        migrations.RunPython(seed_k8s_role_templates, unseed_k8s_role_templates),
    ]
