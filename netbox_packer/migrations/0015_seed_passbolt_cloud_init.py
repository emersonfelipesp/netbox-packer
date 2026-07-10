import hashlib

from django.db import migrations

PASSBOLT_CLOUD_CONFIG = r"""#cloud-config
# Passbolt CE (native APT package) golden template on Ubuntu 24.04 (noble).
# Runs at first boot of a VM cloned from this template (Proxmox cicustom
# user-data). Installs the Passbolt CE server (nginx + php-fpm + local MariaDB)
# for https://credential.nmulti.cloud with JWT authentication enabled. TLS is
# terminated upstream by nginx-nms, so Passbolt serves plain HTTP on :80.
#
# The QEMU guest agent and Zabbix Agent 2 (pointed at zabbix.nmulti.cloud) are
# injected at bake time by netbox-packer (_inject_monitoring_agents); they are
# intentionally NOT installed here. Do not add their package names to this file
# or the build-time injection is skipped.
#
# No secrets are committed: the local MariaDB password is generated on first
# boot into /etc/passbolt/.db_password. The production server OpenPGP key, JWT
# keys, and database content are supplied afterwards by the data migration from
# the existing Passbolt instance (or `cake passbolt install`).
# Template bake identity marker: passbolt-ce-ubuntu-2404.
package_update: true
package_upgrade: true
packages:
  - ca-certificates
  - curl
  - gnupg
  - openssl
  - mariadb-server
write_files:
  - path: /usr/local/sbin/passbolt-ce-bootstrap
    permissions: "0750"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      # First-boot bootstrap for the native Passbolt CE install.
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive

      APP_FULL_BASE_URL="https://credential.nmulti.cloud"
      DB_NAME="passbolt"
      DB_USER="passbolt"
      SECRET_DIR="/etc/passbolt"
      DB_PASS_FILE="${SECRET_DIR}/.db_password"

      install -d -m 0750 "${SECRET_DIR}"

      # 1. Per-instance MariaDB password (generated once; never committed to git
      #    or baked into the image as a literal).
      if [ ! -s "${DB_PASS_FILE}" ]; then
        ( umask 077; openssl rand -hex 24 | tr -d '\n' > "${DB_PASS_FILE}" )
      fi
      DB_PASS="$(cat "${DB_PASS_FILE}")"

      # 2. Bring the local database up before Passbolt configures against it.
      systemctl enable --now mariadb

      # 3. Official Passbolt CE APT repository, added via the checksum-pinned
      #    setup script (downloaded to disk and sha512-verified; no 'curl | bash').
      if ! ls /etc/apt/sources.list.d/passbolt* >/dev/null 2>&1; then
        WORKDIR="$(mktemp -d)"
        cd "${WORKDIR}"
        curl -fsSL -O https://download.passbolt.com/ce/installer/passbolt-repo-setup.ce.sh
        curl -fsSL -O https://github.com/passbolt/passbolt-dep-scripts/releases/latest/download/passbolt-ce-SHA512SUM.txt
        sha512sum -c passbolt-ce-SHA512SUM.txt
        bash ./passbolt-repo-setup.ce.sh
        cd /
        rm -rf "${WORKDIR}"
        apt-get update
      fi

      # 4. Non-interactive install: the package creates the local MariaDB
      #    database + user and an nginx vhost WITHOUT SSL (three-choices=none),
      #    since nginx-nms terminates TLS upstream. Passbolt derives its
      #    fullBaseUrl from the configured domain.
      {
        echo "passbolt-ce-server passbolt/mysql-configuration boolean true"
        echo "passbolt-ce-server passbolt/mysql-passbolt-username string ${DB_USER}"
        echo "passbolt-ce-server passbolt/mysql-passbolt-password password ${DB_PASS}"
        echo "passbolt-ce-server passbolt/mysql-passbolt-password-repeat password ${DB_PASS}"
        echo "passbolt-ce-server passbolt/mysql-passbolt-dbname string ${DB_NAME}"
        echo "passbolt-ce-server passbolt/nginx-configuration boolean true"
        echo "passbolt-ce-server passbolt/nginx-configuration-three-choices select none"
        echo "passbolt-ce-server passbolt/nginx-domain string credential.nmulti.cloud"
      } | debconf-set-selections
      apt-get install -y passbolt-ce-server

      # 5. Force the https base URL and enable JWT auth (required by the
      #    nms-backend Passbolt bridge). SMTP is intentionally left unconfigured.
      {
        printf 'export APP_FULL_BASE_URL=%s\n' "${APP_FULL_BASE_URL}"
        printf '%s\n' 'export PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED=true'
        printf '%s\n' '# SMTP is intentionally unconfigured for now (wire later):'
        printf '%s\n' '# export EMAIL_TRANSPORT_DEFAULT_HOST='
        printf '%s\n' '# export EMAIL_TRANSPORT_DEFAULT_PORT=587'
        printf '%s\n' '# export EMAIL_TRANSPORT_DEFAULT_USERNAME='
        printf '%s\n' '# export EMAIL_TRANSPORT_DEFAULT_PASSWORD='
        printf '%s\n' '# export EMAIL_DEFAULT_FROM=passbolt@nmulti.cloud'
      } > /etc/passbolt/passbolt.env
      chmod 0640 /etc/passbolt/passbolt.env

      # 6. Generate a JWT keypair (best-effort; the production keys are replaced
      #    by the data migration, so a failure here must never abort first boot).
      CAKE="/usr/share/php/passbolt/bin/cake"
      if [ -x "${CAKE}" ]; then
        su -s /bin/bash -c "${CAKE} passbolt create_jwt_keys" www-data || true
      fi

      # 7. Ensure the web stack is enabled.
      systemctl enable --now mariadb || true
      systemctl enable --now php8.3-fpm || true
      systemctl enable --now nginx || true
runcmd:
  - [bash, /usr/local/sbin/passbolt-ce-bootstrap]
"""

CONFIG_NAME = "passbolt-ce-ubuntu-2404"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "passbolt-ce-ubuntu-2404"
# CLUSTER01-DC01 PVE host; proxbox-api SSHes into this host (derived from the
# endpoint hostname) to bake the template.
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"
TEMPLATE_VMID = 9060


def seed_passbolt(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": PASSBOLT_CLOUD_CONFIG,
            "checksum": hashlib.sha256(PASSBOLT_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "Passbolt CE native APT install on Ubuntu 24.04 (nginx + php-fpm "
                "+ local MariaDB) for https://credential.nmulti.cloud with JWT "
                "auth. TLS terminated upstream; QEMU guest agent + Zabbix Agent 2 "
                "injected at bake time."
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
            "install_qemu_guest_agent": True,
            "install_zabbix_agent2": True,
            "zabbix_server": "zabbix.nmulti.cloud",
            "installer_config": config,
            "description": (
                "Passbolt CE cloud-init template image (Ubuntu 24.04) for "
                "credential.nmulti.cloud. Builds via proxbox-api on CLUSTER01-DC01 "
                "(10.0.30.71) storage 'local'. Production instance is finalized by "
                "the data migration from the existing Passbolt instance."
            ),
        },
    )


def unseed_passbolt(apps, schema_editor):
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate.objects.filter(name=TEMPLATE_NAME).delete()
    PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0014_seed_fileserver_allinone_cloud_init"),
    ]

    operations = [
        migrations.RunPython(seed_passbolt, unseed_passbolt),
    ]
