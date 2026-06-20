import hashlib

from django.db import migrations

POWERDNS_AUTH_RECURSOR_CLOUD_CONFIG = """#cloud-config
# PowerDNS Authoritative + Recursor on one Ubuntu 24.04 LTS VM.
# The recursor listens on the VM primary IPv4 address on :53. It is restricted
# to internal/private client ranges and forwards locally-served zones to the
# co-hosted authoritative server on 127.0.0.1:5300.
# Replace PDNS_AUTH_API_KEY, PDNS_RECURSOR_API_KEY, and PDNS_LOCAL_FORWARD_ZONES
# before first production boot. The defaults below are placeholders, not secrets.
package_update: true
package_upgrade: true
packages:
  - pdns-server
  - pdns-backend-sqlite3
  - pdns-recursor
  - qemu-guest-agent
  - sqlite3
  - iproute2
write_files:
  - path: /opt/powerdns-auth-recursor-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive

      AUTH_DB=/var/lib/powerdns/pdns.sqlite3
      AUTH_API_KEY="${PDNS_AUTH_API_KEY:-__SET_PDNS_AUTH_API_KEY_AT_PROVISION__}"
      RECURSOR_API_KEY="${PDNS_RECURSOR_API_KEY:-__SET_PDNS_RECURSOR_API_KEY_AT_PROVISION__}"
      LOCAL_FORWARD_ZONES="${PDNS_LOCAL_FORWARD_ZONES:-nmulti.cloud=127.0.0.1:5300}"
      LOCAL_FORWARD_ZONES_RECURSE="${PDNS_LOCAL_FORWARD_ZONES_RECURSE:-}"
      ALLOW_FROM="127.0.0.1/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128"

      PRIMARY_IPV4="$(ip -4 route get 1.1.1.1 2>/dev/null \\
        | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
      if [ -z "${PRIMARY_IPV4}" ]; then
        PRIMARY_IPV4="$(hostname -I | awk '{print $1}')"
      fi
      if [ -z "${PRIMARY_IPV4}" ]; then
        echo "Unable to determine the VM primary IPv4 address for pdns-recursor" >&2
        exit 1
      fi

      systemctl stop pdns.service pdns-recursor.service 2>/dev/null || true

      # Authoritative server: private loopback listener only. The public :53
      # resolver surface is pdns-recursor, which forwards local zones here.
      cat > /etc/powerdns/pdns.conf <<PDNS_CONF
      launch=gsqlite3
      gsqlite3-database=${AUTH_DB}
      local-address=127.0.0.1
      local-port=5300
      api=yes
      api-key=${AUTH_API_KEY}
      webserver=yes
      webserver-address=127.0.0.1
      webserver-port=8081
      webserver-allow-from=127.0.0.1,::1
      loglevel=4
      log-dns-queries=no
      PDNS_CONF
      chmod 640 /etc/powerdns/pdns.conf
      chown root:pdns /etc/powerdns/pdns.conf

      # Initialize the bundled SQLite3 backend schema exactly once.
      install -d -m 0750 -o pdns -g pdns /var/lib/powerdns
      if ! sqlite3 "${AUTH_DB}" \\
        "SELECT name FROM sqlite_master WHERE type='table' AND name='domains';" | grep -qx domains; then
        SCHEMA_PATH=""
        for candidate in \\
          /usr/share/pdns-backend-sqlite3/schema/schema.sqlite3.sql \\
          /usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql \\
          /usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql.gz; do
          if [ -r "${candidate}" ]; then
            SCHEMA_PATH="${candidate}"
            break
          fi
        done
        if [ -z "${SCHEMA_PATH}" ]; then
          echo "Could not find the pdns-backend-sqlite3 schema file" >&2
          exit 1
        fi
        case "${SCHEMA_PATH}" in
          *.gz) zcat "${SCHEMA_PATH}" | sqlite3 "${AUTH_DB}" ;;
          *) sqlite3 "${AUTH_DB}" < "${SCHEMA_PATH}" ;;
        esac
      fi
      chown pdns:pdns "${AUTH_DB}"
      chmod 640 "${AUTH_DB}"

      # Recursor: listens on the VM primary interface only, never as an open
      # resolver. Locally-served zones are forwarded to authoritative on 5300.
      cat > /etc/powerdns/recursor.conf <<RECURSOR_CONF
      local-address=${PRIMARY_IPV4}
      local-port=53
      allow-from=${ALLOW_FROM}
      forward-zones=${LOCAL_FORWARD_ZONES}
      webserver=yes
      webserver-address=127.0.0.1
      webserver-port=8082
      api-key=${RECURSOR_API_KEY}
      quiet=yes
      loglevel=4
      RECURSOR_CONF
      if [ -n "${LOCAL_FORWARD_ZONES_RECURSE}" ]; then
        printf 'forward-zones-recurse=%s\\n' "${LOCAL_FORWARD_ZONES_RECURSE}" \\
          >> /etc/powerdns/recursor.conf
      fi
      chmod 640 /etc/powerdns/recursor.conf
      chown root:pdns /etc/powerdns/recursor.conf

      systemctl enable pdns.service pdns-recursor.service
      systemctl restart pdns.service pdns-recursor.service
runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]
  - [bash, /opt/powerdns-auth-recursor-bootstrap.sh]
"""

CONFIG_NAME = "powerdns-auth-recursor-ubuntu"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "powerdns-auth-recursor-ubuntu"
TEMPLATE_VMID = 9019
# Real bakes target the CLUSTER01-DC01 PVE cluster host 10.0.30.71 by default.
# Operators may override node and VMID at build dispatch when needed.
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"


def seed_powerdns_auth_recursor(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": POWERDNS_AUTH_RECURSOR_CLOUD_CONFIG,
            "checksum": hashlib.sha256(POWERDNS_AUTH_RECURSOR_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "PowerDNS Authoritative + Recursor co-hosted on Ubuntu 24.04. "
                "Uses the SQLite3 authoritative backend, keeps pdns-auth on "
                "127.0.0.1:5300, exposes REST APIs on localhost only, and restricts "
                "recursive clients to internal/private ranges."
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
                "PowerDNS Authoritative + Recursor template (Ubuntu 24.04, VMID 9019). "
                "Bakes via proxbox-api on CLUSTER01-DC01 host 10.0.30.71 using storage "
                "'local'. Override API-key placeholders and forwarded local zones before "
                "production use."
            ),
        },
    )


def unseed_powerdns_auth_recursor(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    PackerTemplate.objects.filter(name=TEMPLATE_NAME).delete()
    PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0012_seed_powerdns_cloud_init"),
    ]

    operations = [
        migrations.RunPython(seed_powerdns_auth_recursor, unseed_powerdns_auth_recursor),
    ]
