import hashlib

from django.db import migrations

ZABBIX_CLOUD_CONFIG = """#cloud-config
# Zabbix 7.4 - server + frontend + agent2 on Ubuntu 26.04, PostgreSQL + nginx (PHP 8.5).
# Applied to cloned VMs via Proxmox cicustom user-data at first boot.
# ZABBIX_DB_PASSWORD is a placeholder ('zabbix'); override per-VM before production use.
package_update: true
package_upgrade: true
packages:
  - curl
  - ca-certificates
  - postgresql
  - postgresql-contrib
write_files:
  - path: /opt/zabbix-bootstrap.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive
      DB_PASSWORD="${ZABBIX_DB_PASSWORD:-zabbix}"
      # a. Zabbix 7.4 repository
      curl -fsSL -o /tmp/zabbix-release.deb \\
        https://repo.zabbix.com/zabbix/7.4/release/ubuntu/pool/main/z/zabbix-release/zabbix-release_latest_7.4+ubuntu26.04_all.deb
      dpkg -i /tmp/zabbix-release.deb
      apt-get update
      # b. Server, frontend, agent2
      apt-get install -y zabbix-server-pgsql zabbix-frontend-php php8.5-pgsql \\
        zabbix-nginx-conf zabbix-sql-scripts zabbix-agent2
      # c. Agent 2 plugins
      apt-get install -y zabbix-agent2-plugin-mongodb zabbix-agent2-plugin-mssql \\
        zabbix-agent2-plugin-postgresql
      # d. Initial database (local PostgreSQL)
      sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='zabbix'" | grep -q 1 || \\
        sudo -u postgres psql -c "CREATE ROLE zabbix LOGIN PASSWORD '${DB_PASSWORD}';"
      sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='zabbix'" | grep -q 1 || \\
        sudo -u postgres createdb -O zabbix zabbix
      zcat /usr/share/zabbix/sql-scripts/postgresql/server.sql.gz | sudo -u zabbix psql zabbix
      # e. Configure zabbix_server.conf
      if grep -q '^DBPassword=' /etc/zabbix/zabbix_server.conf; then
        sed -i "s|^DBPassword=.*|DBPassword=${DB_PASSWORD}|" /etc/zabbix/zabbix_server.conf
      else
        echo "DBPassword=${DB_PASSWORD}" >> /etc/zabbix/zabbix_server.conf
      fi
      # f. nginx vhost shipped by zabbix-nginx-conf (uncomment listen + server_name)
      sed -i 's|^\\s*#\\s*listen\\s\\+8080;|        listen 80;|' /etc/zabbix/nginx.conf
      sed -i 's|^\\s*#\\s*server_name\\s\\+example.com;|        server_name _;|' /etc/zabbix/nginx.conf
      rm -f /etc/nginx/sites-enabled/default || true
      # g. Start + enable
      systemctl restart zabbix-server zabbix-agent2 nginx php8.5-fpm
      systemctl enable zabbix-server zabbix-agent2 nginx php8.5-fpm
runcmd:
  - [bash, /opt/zabbix-bootstrap.sh]
"""

CONFIG_NAME = "zabbix-7.4-ubuntu-2604-pgsql-nginx"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "zabbix-7.4-ubuntu-2604-pgsql-nginx"
# Proxmox host registered as a netbox-proxbox ProxmoxEndpoint; proxbox-api SSHes
# into this host (derived from the endpoint hostname) to bake the template.
PROXMOX_ENDPOINT = "https://10.0.30.139:8006"
TEMPLATE_VMID = 9010


def seed_zabbix(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": ZABBIX_CLOUD_CONFIG,
            "checksum": hashlib.sha256(ZABBIX_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "Zabbix 7.4 server + frontend + agent2 on Ubuntu 26.04 with "
                "PostgreSQL + nginx (PHP 8.5). Baked as a Proxmox cicustom user "
                "snippet via proxbox-api."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=TEMPLATE_NAME,
        defaults={
            "os_family": "ubuntu",
            "os_version": "26.04",
            "proxmox_template_id": TEMPLATE_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": "",
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": "",
            "installer_config": config,
            "description": (
                "Zabbix 7.4 cloud-init template image (Ubuntu 26.04). Builds via "
                "proxbox-api on the ProxmoxEndpoint host using storage 'local'. "
                "Requires PackerPluginSettings.proxbox_api_url + key."
            ),
        },
    )


def unseed_zabbix(apps, schema_editor):
    # Intentionally a no-op: do not delete operator data on reverse migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0005_packerpluginsettings_proxbox_api"),
    ]

    operations = [
        migrations.RunPython(seed_zabbix, unseed_zabbix),
    ]
