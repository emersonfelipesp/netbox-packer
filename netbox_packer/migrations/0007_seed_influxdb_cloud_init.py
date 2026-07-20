import hashlib

from django.db import migrations

INFLUXDB_CLOUD_CONFIG = """#cloud-config
# InfluxDB 2.x - Proxmox metrics collector on Ubuntu 24.04.
# Applied to cloned VMs via Proxmox cicustom user-data at first boot.
# Override INFLUXDB_USERNAME, INFLUXDB_PASSWORD, INFLUXDB_ORG, INFLUXDB_BUCKET,
# INFLUXDB_RETENTION_SECONDS, or INFLUXDB_ADMIN_TOKEN per clone before production use.
package_update: true
package_upgrade: true
packages:
  - ca-certificates
  - curl
  - gnupg
  - jq
  - openssl
  - qemu-guest-agent
write_files:
  - path: /opt/influxdb-collector-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive
      install -d -m 0755 /etc/apt/keyrings /etc/nmulticloud

      curl --silent --location -o /tmp/influxdata-archive.key \\
        https://repos.influxdata.com/influxdata-archive.key
      gpg --show-keys --with-fingerprint --with-colons /tmp/influxdata-archive.key 2>&1 \\
        | grep -q '^fpr:\\+24C975CBA61A024EE1B631787C3D57159FC2F927:$'
      gpg --dearmor < /tmp/influxdata-archive.key > /etc/apt/keyrings/influxdata-archive.gpg
      echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \\
        > /etc/apt/sources.list.d/influxdata.list

      apt-get update
      apt-get install -y influxdb2
      systemctl enable --now qemu-guest-agent
      systemctl enable --now influxdb

      for _ in $(seq 1 60); do
        curl -fsS http://127.0.0.1:8086/health && break
        sleep 2
      done

      USERNAME="${INFLUXDB_USERNAME:-admin}"
      PASSWORD="${INFLUXDB_PASSWORD:-$(openssl rand -base64 24)}"
      ORG="${INFLUXDB_ORG:-nmulticloud}"
      BUCKET="${INFLUXDB_BUCKET:-proxmox}"
      RETENTION_SECONDS="${INFLUXDB_RETENTION_SECONDS:-2592000}"
      case "${RETENTION_SECONDS}" in
        ""|*[!0-9]*) RETENTION_SECONDS="2592000" ;;
      esac
      TOKEN="${INFLUXDB_ADMIN_TOKEN:-$(openssl rand -hex 32)}"
      SETUP_ALLOWED="$(curl -fsS http://127.0.0.1:8086/api/v2/setup | jq -r '.allowed // true')"

      if [ "${SETUP_ALLOWED}" = "true" ]; then
        SETUP_BODY="$(jq -n \\
          --arg username "${USERNAME}" \\
          --arg password "${PASSWORD}" \\
          --arg org "${ORG}" \\
          --arg bucket "${BUCKET}" \\
          --arg token "${TOKEN}" \\
          --argjson retentionPeriodSeconds "${RETENTION_SECONDS}" \\
          '{
            username: $username,
            password: $password,
            org: $org,
            bucket: $bucket,
            retentionPeriodSeconds: $retentionPeriodSeconds,
            token: $token
          }')"
        curl -fsS -X POST http://127.0.0.1:8086/api/v2/setup \\
          -H 'Content-Type: application/json' \\
          --data "${SETUP_BODY}"
      fi

      cat > /etc/nmulticloud/influxdb-collector.env <<EOF
      INFLUXDB_URL=http://$(hostname -I | awk '{print $1}'):8086
      INFLUXDB_ORG=${ORG}
      INFLUXDB_BUCKET=${BUCKET}
      INFLUXDB_ADMIN_USERNAME=${USERNAME}
      INFLUXDB_ADMIN_PASSWORD=${PASSWORD}
      INFLUXDB_ADMIN_TOKEN=${TOKEN}
      EOF
      chmod 600 /etc/nmulticloud/influxdb-collector.env
      systemctl restart influxdb
runcmd:
  - [bash, /opt/influxdb-collector-bootstrap.sh]
"""

CONFIG_NAME = "influxdb-2-ubuntu-2404-proxmox-collector"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "influxdb-2-ubuntu-2404-proxmox-collector"
# Development Proxmox endpoint only (NetBox ProxmoxEndpoint ID 11).
# Do not seed or build this template on 10.0.30.9.
PROXMOX_ENDPOINT = "https://10.0.30.58:8006"
TEMPLATE_VMID = 9011


def seed_influxdb(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": INFLUXDB_CLOUD_CONFIG,
            "checksum": hashlib.sha256(INFLUXDB_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "InfluxDB 2.x collector image for Proxmox cluster metrics on Ubuntu "
                "24.04. Baked as a Proxmox cicustom user snippet via proxbox-api."
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
            "proxmox_node": "10.0.30.58",
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": config,
            "description": (
                "InfluxDB 2.x cloud-init template image (Ubuntu 24.04). Builds via "
                "proxbox-api on development ProxmoxEndpoint ID 11 (10.0.30.58) using "
                "storage 'local'. Do not target the production 10.0.30.9 cluster."
            ),
        },
    )


def unseed_influxdb(apps, schema_editor):
    # Intentionally a no-op: do not delete operator data on reverse migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0006_seed_zabbix_cloud_init"),
    ]

    operations = [
        migrations.RunPython(seed_influxdb, unseed_influxdb),
    ]
