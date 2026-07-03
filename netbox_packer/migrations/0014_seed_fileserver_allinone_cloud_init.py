import hashlib

from django.db import migrations

FILESERVER_ALLINONE_CLOUD_CONFIG = """#cloud-config
# File Server all-in-one golden template on Ubuntu 24.04.
# Bakes only shared software and production agent endpoints. Tenant-specific
# Samba domain provisioning, Nextcloud installation, and enrollment tokens are
# provided by clone-time user-data/runtime automation.
# Template bake identity marker: tpl-fileserver-allinone.
package_update: true
package_upgrade: true
packages:
  - acl
  - apt-transport-https
  - attr
  - ca-certificates
  - chrony
  - cifs-utils
  - curl
  - gnupg
  - krb5-user
  - libnss-winbind
  - libpam-winbind
  - nginx
  - php-bcmath
  - php-cli
  - php-curl
  - php-fpm
  - php-gd
  - php-gmp
  - php-imagick
  - php-intl
  - php-ldap
  - php-mbstring
  - php-pgsql
  - php-smbclient
  - php-xml
  - php-zip
  - postgresql-client
  - python3-venv
  - qemu-guest-agent
  - samba
  - samba-dsdb-modules
  - samba-vfs-modules
  - smbclient
  - unzip
  - winbind
write_files:
  - path: /etc/nms-fileserver-agent/config.env
    permissions: "0640"
    owner: root:root
    content: |
      NMS_BACKEND_URL=https://backend.nms.nmulti.cloud
      NETBOX_URL=https://netbox.nmulti.cloud
      # Enrollment token is intentionally not baked into the golden image.
      # Clone-time cloud-init user-data writes the per-instance one-time token.
  - path: /etc/cloud/cloud.cfg.d/99-fileserver-allinone-template.cfg
    permissions: "0644"
    owner: root:root
    content: |
      # Static template identity marker for the image bake.
      # Proxmox clone metadata and per-instance user-data provide runtime identity.
      preserve_hostname: false
  - path: /etc/systemd/system/nms-fileserver-agent-enroll.service
    permissions: "0644"
    owner: root:root
    content: |
      [Unit]
      Description=N-MultiCloud File Server agent enrollment
      Wants=network-online.target
      After=network-online.target cloud-final.service

      [Service]
      Type=oneshot
      ExecStart=/opt/nms-fileserver-agent/venv/bin/nms-fileserver-agent-enroll
      NoNewPrivileges=true
      PrivateTmp=true
      ProtectHome=true
      ProtectSystem=full
      ReadWritePaths=/etc/nms-fileserver-agent
      LockPersonality=true
      RestrictSUIDSGID=true

      [Install]
      WantedBy=multi-user.target
  - path: /etc/systemd/system/nms-fileserver-agent-heartbeat.service
    permissions: "0644"
    owner: root:root
    content: |
      [Unit]
      Description=N-MultiCloud File Server agent heartbeat
      Wants=network-online.target
      After=network-online.target nms-fileserver-agent-enroll.service

      [Service]
      Type=oneshot
      ExecStart=/opt/nms-fileserver-agent/venv/bin/nms-fileserver-agent-heartbeat
      NoNewPrivileges=true
      PrivateTmp=true
      ProtectHome=true
      ProtectSystem=full
      ReadWritePaths=/etc/nms-fileserver-agent
      LockPersonality=true
      RestrictSUIDSGID=true
  - path: /etc/systemd/system/nms-fileserver-agent-heartbeat.timer
    permissions: "0644"
    owner: root:root
    content: |
      [Unit]
      Description=Run N-MultiCloud File Server agent heartbeat every five minutes

      [Timer]
      OnBootSec=5min
      OnUnitActiveSec=5min
      AccuracySec=30s
      Persistent=true
      Unit=nms-fileserver-agent-heartbeat.service

      [Install]
      WantedBy=timers.target
  - path: /opt/fileserver-allinone-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive

      install -d -m 0755 /etc/apt/keyrings
      install -d -m 0755 /opt/nms-fileserver-agent
      install -d -m 0700 /etc/nms-fileserver-agent

      . /etc/os-release
      VERSION_ID="${VERSION_ID:-24.04}"
      NMS_FILESERVER_AGENT_INSTALL_DIR="${NMS_FILESERVER_AGENT_INSTALL_DIR:-/opt/nms-fileserver-agent}"
      NMS_FILESERVER_AGENT_DIR="${NMS_FILESERVER_AGENT_DIR:-/etc/nms-fileserver-agent}"
      NMS_FILESERVER_AGENT_PIP_SPEC="${NMS_FILESERVER_AGENT_PIP_SPEC:-nms-fileserver-agent==0.1.0}"
      NMS_FILESERVER_AGENT_VENV_DIR="${NMS_FILESERVER_AGENT_INSTALL_DIR}/venv"
      ZABBIX_RELEASE_BASE="https://repo.zabbix.com/zabbix/7.4/release/ubuntu/pool/main/z/zabbix-release"
      ZABBIX_RELEASE_DEB="${ZABBIX_RELEASE_BASE}/zabbix-release_latest_7.4+ubuntu${VERSION_ID}_all.deb"
      curl -fsSL -o /tmp/zabbix-release.deb "${ZABBIX_RELEASE_DEB}"
      dpkg -i /tmp/zabbix-release.deb
      apt-get update
      apt-get install -y zabbix-agent2

      python3 -m venv "${NMS_FILESERVER_AGENT_VENV_DIR}"
      "${NMS_FILESERVER_AGENT_VENV_DIR}/bin/python" -m pip install --upgrade pip
      "${NMS_FILESERVER_AGENT_VENV_DIR}/bin/python" -m pip install "${NMS_FILESERVER_AGENT_PIP_SPEC}"

      cat > /etc/zabbix/zabbix_agent2.conf <<'ZABBIX_CONF'
      LogFile=/var/log/zabbix/zabbix_agent2.log
      LogFileSize=0
      PidFile=/run/zabbix/zabbix_agent2.pid
      Server=zabbix.nmulti.cloud
      ServerActive=zabbix.nmulti.cloud
      HostnameItem=system.hostname
      Include=/etc/zabbix/zabbix_agent2.d/*.conf
      PluginSocket=/run/zabbix/agent.plugin.sock
      ZABBIX_CONF

      chown root:root /etc/nms-fileserver-agent/config.env
      chmod 0640 /etc/nms-fileserver-agent/config.env
      for state_file in enroll-token credential deployment-id; do
        if [[ -f "${NMS_FILESERVER_AGENT_DIR}/${state_file}" ]]; then
          chmod 0600 "${NMS_FILESERVER_AGENT_DIR}/${state_file}"
        fi
      done

      systemctl daemon-reload

      systemctl enable --now qemu-guest-agent
      systemctl enable --now chrony
      systemctl enable --now zabbix-agent2
      systemctl enable nms-fileserver-agent-enroll.service
      systemctl enable --now nms-fileserver-agent-heartbeat.timer

      # Keep the unprovisioned appliance dark until runtime provisioning supplies tenant state.
      systemctl disable --now nginx || true
      systemctl disable --now nms-fileserver-agent-enroll.service || true
      systemctl disable --now nms-fileserver-agent-heartbeat.timer || true
      systemctl disable --now nms-fileserver-agent-heartbeat.service || true
      for unit in smbd nmbd winbind samba-ad-dc; do
        systemctl disable --now "${unit}" || true
      done
      systemctl mask smbd nmbd winbind || true
      for unit in $(systemctl list-unit-files --type=service --no-legend 'php*-fpm.service' | awk '{print $1}'); do
        systemctl disable --now "${unit}" || true
      done

      echo "fileserver all-in-one software bake complete"
runcmd:
  - [bash, /opt/fileserver-allinone-bootstrap.sh]
"""

CONFIG_NAME = "fileserver-allinone-cloud-config"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "tpl-fileserver-allinone-ubuntu-2404"
TEMPLATE_VMID = 9032
# Real bakes target the CLUSTER01-DC01 PVE cluster host 10.0.30.71 by default.
# Operators may override node and VMID at build dispatch when needed.
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"


def seed_fileserver_allinone(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": FILESERVER_ALLINONE_CLOUD_CONFIG,
            "checksum": hashlib.sha256(FILESERVER_ALLINONE_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "File Server all-in-one cloud-config on Ubuntu 24.04. Installs "
                "Samba AD/DC packages, Nextcloud web/PHP prerequisites, monitoring "
                "agents, and a pip-installed nms-fileserver-agent with production "
                "NMS URLs. Tenant provisioning and enrollment tokens are supplied "
                "at clone time."
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
            "proxmox_node": PROXMOX_NODE,
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": config,
            "install_qemu_guest_agent": True,
            "install_zabbix_agent2": True,
            "zabbix_server": "zabbix.nmulti.cloud",
            "description": (
                "File Server all-in-one template (Ubuntu 24.04, VMID 9032). Bakes "
                "via proxbox-api on CLUSTER01-DC01 host 10.0.30.71 using storage "
                "'local'. Runtime automation provisions Samba, Nextcloud, and "
                "the one-time nms-fileserver-agent enrollment token per clone."
            ),
        },
    )


def unseed_fileserver_allinone(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    PackerTemplate.objects.filter(name=TEMPLATE_NAME).delete()
    PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0013_seed_powerdns_auth_recursor_cloud_init"),
    ]

    operations = [
        migrations.RunPython(seed_fileserver_allinone, unseed_fileserver_allinone),
    ]
