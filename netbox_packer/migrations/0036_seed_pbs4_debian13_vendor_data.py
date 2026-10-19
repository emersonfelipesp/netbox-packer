"""Seed the Proxmox Backup Server 4 template for Debian 13."""

import hashlib

from django.db import migrations
from django.db.migrations.recorder import MigrationRecorder

from netbox_packer.migrations._legacy_agent_columns import ensure_legacy_column_defaults

VENDOR_DATA = r"""#cloud-config
disable_root: false
package_update: true
package_upgrade: false
packages:
  - ca-certificates
  - curl
write_files:
  - path: /usr/local/sbin/install-proxmox-backup-server
    owner: root:root
    permissions: "0700"
    content: |
      #!/usr/bin/env bash
      set -eu
      set -o pipefail
      export DEBIAN_FRONTEND=noninteractive

      readonly KEY_URL='https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg'
      readonly KEY_SHA256='136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45'
      readonly KEYRING='/usr/share/keyrings/proxmox-archive-keyring.gpg'
      readonly SOURCE='/etc/apt/sources.list.d/pbs-no-subscription.sources'
      readonly LOG='/var/log/netbox-pbs-install.log'
      key_tmp="$(mktemp)"
      cleanup() { rm -f "${key_tmp}"; }
      trap cleanup EXIT

      curl --fail --silent --show-error --location \
        --proto '=https' --tlsv1.2 --retry 3 --retry-delay 2 \
        --retry-max-time 60 --connect-timeout 10 --max-time 60 \
        --max-filesize 1048576 --output "${key_tmp}" "${KEY_URL}"
      printf '%s  %s\n' "${KEY_SHA256}" "${key_tmp}" | sha256sum --check --strict -
      install -o root -g root -m 0644 "${key_tmp}" "${KEYRING}"

      cat > "${SOURCE}" <<'SOURCES'
      Types: deb
      URIs: http://download.proxmox.com/debian/pbs
      Suites: trixie
      Components: pbs-no-subscription
      Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
      SOURCES

      # The package installs its enterprise repository enabled. That repository
      # needs a subscription, so apt-get update fails while it is enabled. Move
      # the dedicated enterprise files out of the names apt reads instead of
      # editing them: any stanza, field case, or whitespace is then irrelevant,
      # and dpkg keeps a removed conffile removed on later upgrades. Do this
      # before the first update (a partial install by an earlier run may have
      # left the file behind) and again after the package transaction, whatever
      # its outcome.
      disable_enterprise_sources() {
        for enterprise in /etc/apt/sources.list.d/pbs-enterprise.sources \
          /etc/apt/sources.list.d/pbs-enterprise.list; do
          [ -f "${enterprise}" ] || continue
          mv -f "${enterprise}" "${enterprise}.disabled"
        done
      }

      # --error-on=any makes a transient fetch failure fatal. A plain update can
      # exit zero and keep stale indexes, which would hide an unreachable source.
      disable_enterprise_sources
      apt-get --error-on=any update
      install_status=0
      apt-get install -y proxmox-backup-server qemu-guest-agent || install_status=$?
      disable_enterprise_sources
      if [ "${install_status}" -ne 0 ]; then
        echo 'Installing Proxmox Backup Server failed.' >&2
        exit "${install_status}"
      fi
      apt-get --error-on=any update

      systemctl enable --now proxmox-backup proxmox-backup-proxy qemu-guest-agent

      deadline=$((SECONDS + 120))
      until ss -H -ltn | awk '{print $4}' | grep -Eq '(^|:)8007$'; do
        if [ "${SECONDS}" -ge "${deadline}" ]; then
          echo 'Timed out waiting for Proxmox Backup Server port 8007.' >&2
          exit 1
        fi
        sleep 2
      done
      printf '%s\n' 'NETBOX_PBS_INSTALL_COMPLETE' >> "${LOG}"
runcmd:
  - [/usr/local/sbin/install-proxmox-backup-server]
"""
CONFIG_NAME = "pbs-4-debian-13-vendor-data"
CONFIG_VERSION = "1.0.0"
TEMPLATE_NAME = "pbs-4-debian-13"
TEMPLATE_VMID = 9061
_MUTABLE_BUILD_STATE_FIELDS = frozenset({"installer_config", "build_status", "packer_template_ref"})
PREVIOUS_MIGRATION = ("netbox_packer", "0035_seed_pbs4_debian13_vendor_data")


def seed_pbs4_debian13(apps, schema_editor) -> None:
    ensure_legacy_column_defaults(apps, schema_editor)
    if PREVIOUS_MIGRATION in MigrationRecorder(schema_editor.connection).applied_migrations():
        return

    Installer = apps.get_model("netbox_packer", "PackerInstallerConfig")
    Template = apps.get_model("netbox_packer", "PackerTemplate")
    config_defaults = {
        "os_family": "debian",
        "installer_type": "vendor_data",
        "content": VENDOR_DATA,
        "checksum": hashlib.sha256(VENDOR_DATA.encode()).hexdigest(),
        "description": (
            "Proxmox Backup Server 4 vendor-data for Debian 13. It contains no per-VM identity or credential fields."
        ),
    }
    config, created = Installer.objects.get_or_create(
        name=CONFIG_NAME, version=CONFIG_VERSION, defaults=config_defaults
    )
    if not created:
        mismatches = [field for field, expected in config_defaults.items() if getattr(config, field) != expected]
        if mismatches:
            raise RuntimeError(
                f"PBS 4 Debian 13 seed naming collision: installer config {CONFIG_NAME!r} "
                f"version {CONFIG_VERSION!r} differs in {', '.join(mismatches)}. No existing row was modified."
            )

    template_defaults = {
        "os_family": "debian",
        "os_version": "13",
        "proxmox_template_id": TEMPLATE_VMID,
        "proxmox_endpoint": "",
        "proxmox_node": "select-at-build",
        "storage_pool": "local",
        "cloud_init_ready": True,
        "build_status": "pending",
        "packer_template_ref": "",
        "install_qemu_guest_agent": True,
        "install_zabbix_agent2": False,
        "installer_config": config,
        "description": (
            "Proxmox Backup Server 4 template for Debian 13, VMID 9061. "
            "Vendor-data installs PBS while generated per-VM Cloud-Init user-data keeps the root password and SSH keys."
        ),
    }
    template, created = Template.objects.get_or_create(name=TEMPLATE_NAME, defaults=template_defaults)
    if created:
        return
    expected = {field: value for field, value in template_defaults.items() if field not in _MUTABLE_BUILD_STATE_FIELDS}
    expected["installer_config_id"] = config.pk
    mismatches = [field for field, value in expected.items() if getattr(template, field) != value]
    if mismatches:
        raise RuntimeError(
            f"PBS 4 Debian 13 seed naming collision: template {TEMPLATE_NAME!r} differs in "
            f"{', '.join(mismatches)}. No existing row was modified."
        )


def unseed_pbs4_debian13(apps, schema_editor) -> None:
    pass


class Migration(migrations.Migration):
    dependencies = [("netbox_packer", "0035_alter_packerinstallerconfig_installer_type")]
    operations = [migrations.RunPython(seed_pbs4_debian13, unseed_pbs4_debian13)]
