import hashlib

from django.db import migrations

# ---------------------------------------------------------------------------
# PowerDNS Authoritative Server 4.9 cloud-config
# ---------------------------------------------------------------------------
PDNS_AUTH_CLOUD_CONFIG = """#cloud-config
# PowerDNS Authoritative Server 4.9 on Ubuntu 24.04 LTS.
# DNS domain: nmulti.cloud. Nameservers: 168.0.96.26, 168.0.96.27.
# Zabbix Agent 2 (zabbix.nmulti.cloud) and QEMU guest agent are injected
# by _inject_monitoring_agents at build time.
package_update: true
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - sqlite3
write_files:
  - path: /etc/systemd/resolved.conf.d/nmulticloud.conf
    permissions: "0644"
    owner: root:root
    content: |
      [Resolve]
      DNS=168.0.96.26 168.0.96.27
      Domains=nmulti.cloud
      # Disable the systemd-resolved stub on 127.0.0.53:53 so PowerDNS can bind 0.0.0.0:53.
      DNSStubListener=no
  - path: /etc/apt/sources.list.d/pdns.sources
    permissions: "0644"
    owner: root:root
    content: |
      Types: deb
      URIs: https://repo.powerdns.com/ubuntu
      Suites: noble-auth-49
      Components: main
      Signed-By: /etc/apt/keyrings/pdns.asc
  - path: /etc/apt/preferences.d/pdns
    permissions: "0644"
    owner: root:root
    content: |
      Package: pdns-*
      Pin: origin repo.powerdns.com
      Pin-Priority: 600
  - path: /opt/pdns-auth-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive

      # 1. PowerDNS Authoritative 4.9 APT signing key
      install -d -m 0755 /etc/apt/keyrings
      curl -sSfL -o /etc/apt/keyrings/pdns.asc \\
        https://repo.powerdns.com/FD380FBB-pub.asc

      # 2. Install pdns-server and SQLite3 backend
      apt-get update
      apt-get install -y pdns-server pdns-backend-sqlite3

      # 3. Write minimal server config
      cat > /etc/powerdns/pdns.conf << 'PDNSCONF'
      launch=gsqlite3
      gsqlite3-database=/var/lib/powerdns/pdns.sqlite3
      local-address=0.0.0.0
      local-port=53
      api=yes
      api-key=changeme-override-before-production
      webserver=yes
      webserver-address=0.0.0.0
      webserver-port=8081
      webserver-allow-from=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
      loglevel=4
      log-dns-queries=no
      PDNSCONF
      chmod 640 /etc/powerdns/pdns.conf
      chown root:pdns /etc/powerdns/pdns.conf

      # 4. Initialize SQLite backend schema (path is resolved dynamically so the
      #    bootstrap survives pdns-backend-sqlite3 packaging-path changes).
      mkdir -p /var/lib/powerdns
      SCHEMA=$(find /usr/share -name 'schema.sqlite3.sql' 2>/dev/null | head -1)
      sqlite3 /var/lib/powerdns/pdns.sqlite3 < "$SCHEMA"
      chown -R pdns:pdns /var/lib/powerdns

      # 5. Apply DNS resolver config and start service
      systemctl restart systemd-resolved
      systemctl enable pdns.service
      systemctl start pdns.service
runcmd:
  - [bash, /opt/pdns-auth-bootstrap.sh]
"""

# ---------------------------------------------------------------------------
# PowerDNS Recursor 5.1 cloud-config
# ---------------------------------------------------------------------------
PDNS_RECURSOR_CLOUD_CONFIG = """#cloud-config
# PowerDNS Recursor 5.1 on Ubuntu 24.04 LTS.
# DNS domain: nmulti.cloud. Nameservers: 168.0.96.26, 168.0.96.27.
# Configured as a caching forwarder: all queries forwarded recursively to
# 168.0.96.26 and 168.0.96.27.
# Zabbix Agent 2 (zabbix.nmulti.cloud) and QEMU guest agent are injected
# by _inject_monitoring_agents at build time.
package_update: true
package_upgrade: false
packages:
  - ca-certificates
  - curl
write_files:
  - path: /etc/systemd/resolved.conf.d/nmulticloud.conf
    permissions: "0644"
    owner: root:root
    content: |
      [Resolve]
      DNS=168.0.96.26 168.0.96.27
      Domains=nmulti.cloud
      # Disable the systemd-resolved stub on 127.0.0.53:53 so PowerDNS can bind 0.0.0.0:53.
      DNSStubListener=no
  - path: /etc/apt/sources.list.d/pdns.sources
    permissions: "0644"
    owner: root:root
    content: |
      Types: deb
      URIs: https://repo.powerdns.com/ubuntu
      Suites: noble-rec-51
      Components: main
      Signed-By: /etc/apt/keyrings/pdns.asc
  - path: /etc/apt/preferences.d/pdns
    permissions: "0644"
    owner: root:root
    content: |
      Package: pdns-recursor
      Pin: origin repo.powerdns.com
      Pin-Priority: 600
  - path: /opt/pdns-recursor-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive

      # 1. PowerDNS Recursor 5.1 APT signing key
      install -d -m 0755 /etc/apt/keyrings
      curl -sSfL -o /etc/apt/keyrings/pdns.asc \\
        https://repo.powerdns.com/FD380FBB-pub.asc

      # 2. Install pdns-recursor
      apt-get update
      apt-get install -y pdns-recursor

      # 3. Write minimal recursor config
      cat > /etc/powerdns/recursor.conf << 'RCONF'
      allow-from=127.0.0.1/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1/128
      forward-zones-recurse=.=168.0.96.26;168.0.96.27
      local-address=0.0.0.0
      local-port=53
      dnssec=off
      loglevel=4
      quiet=yes
      RCONF
      chmod 640 /etc/powerdns/recursor.conf
      chown root:pdns /etc/powerdns/recursor.conf

      # 4. Apply DNS resolver config and start service
      systemctl restart systemd-resolved
      systemctl enable pdns-recursor.service
      systemctl start pdns-recursor.service
runcmd:
  - [bash, /opt/pdns-recursor-bootstrap.sh]
"""

# Template metadata
AUTH_CONFIG_NAME = "pdns-auth-ubuntu-2404"
AUTH_CONFIG_VERSION = "1.0.0"
AUTH_TEMPLATE_NAME = "pdns-auth-ubuntu-2404"
AUTH_VMID = 9017

RECURSOR_CONFIG_NAME = "pdns-recursor-ubuntu-2404"
RECURSOR_CONFIG_VERSION = "1.0.0"
RECURSOR_TEMPLATE_NAME = "pdns-recursor-ubuntu-2404"
RECURSOR_VMID = 9018

CONFIG_VERSION = "1.0.0"
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"


def seed_powerdns(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    # --- PowerDNS Authoritative ---
    auth_config, _ = PackerInstallerConfig.objects.get_or_create(
        name=AUTH_CONFIG_NAME,
        version=AUTH_CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": PDNS_AUTH_CLOUD_CONFIG,
            "checksum": hashlib.sha256(PDNS_AUTH_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "PowerDNS Authoritative Server 4.9 cloud-config on Ubuntu 24.04. "
                "Installs pdns-server + pdns-backend-sqlite3 from the official "
                "PowerDNS APT repo (noble-auth-49). Initializes an SQLite3 backend, "
                "enables the REST API on port 8081, and configures DNS domain "
                "nmulti.cloud with nameservers 168.0.96.26 and 168.0.96.27 via "
                "systemd-resolved. Baked on ProxmoxEndpoint 10.0.30.71."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=AUTH_TEMPLATE_NAME,
        defaults={
            "os_family": "ubuntu",
            "os_version": "24.04",
            "proxmox_template_id": AUTH_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": PROXMOX_NODE,
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": auth_config,
            "install_qemu_guest_agent": True,
            "install_zabbix_agent2": True,
            "zabbix_server": "zabbix.nmulti.cloud",
            "description": (
                "PowerDNS Authoritative Server 4.9 template (Ubuntu 24.04, VMID 9017). "
                "Baked via proxbox-api on ProxmoxEndpoint 10.0.30.71 using storage "
                "'local'. Provides an authoritative DNS server with SQLite3 backend "
                "and REST API pre-installed. Override api-key before production use."
            ),
        },
    )

    # --- PowerDNS Recursor ---
    recursor_config, _ = PackerInstallerConfig.objects.get_or_create(
        name=RECURSOR_CONFIG_NAME,
        version=RECURSOR_CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": PDNS_RECURSOR_CLOUD_CONFIG,
            "checksum": hashlib.sha256(PDNS_RECURSOR_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "PowerDNS Recursor 5.1 cloud-config on Ubuntu 24.04. "
                "Installs pdns-recursor from the official PowerDNS APT repo "
                "(noble-rec-51). Configured as a caching forwarder: all queries "
                "recursively forwarded to 168.0.96.26 and 168.0.96.27. DNS domain "
                "nmulti.cloud via systemd-resolved. Baked on ProxmoxEndpoint 10.0.30.71."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=RECURSOR_TEMPLATE_NAME,
        defaults={
            "os_family": "ubuntu",
            "os_version": "24.04",
            "proxmox_template_id": RECURSOR_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": PROXMOX_NODE,
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": recursor_config,
            "install_qemu_guest_agent": True,
            "install_zabbix_agent2": True,
            "zabbix_server": "zabbix.nmulti.cloud",
            "description": (
                "PowerDNS Recursor 5.1 template (Ubuntu 24.04, VMID 9018). "
                "Baked via proxbox-api on ProxmoxEndpoint 10.0.30.71 using storage "
                "'local'. Provides a caching recursive DNS forwarder pointing at "
                "168.0.96.26 and 168.0.96.27 for all zones."
            ),
        },
    )


def unseed_powerdns(apps, schema_editor):
    # Intentionally a no-op: do not delete operator data on reverse migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0011_seed_k8s_role_templates"),
    ]

    operations = [
        migrations.RunPython(seed_powerdns, unseed_powerdns),
    ]
