"""Seed the InfluxDB 3 Core 3.11.0 Debian 13 production cloud-init template.

The existing InfluxDB 3 Core profile (migration ``0020``) targets Ubuntu 24.04 and
leaves bind address, node id, data directory, telemetry, plugin directory, logging,
and WAL flush interval at package defaults. This profile adds Debian 13 and bakes
the production posture of the operator installer instead: loopback-only bind with
token authentication enabled, telemetry upload disabled, Processing Engine
disabled, an explicit managed configuration file, a systemd drop-in, and a held
package.

It stays credential-free. The first administrative token is created and vaulted
only by typed NMS RPC (``service.influxdb.1.bootstrap``, ``family="core3"``),
mirroring the decision recorded in ``0020``; the paired audited procedures
``os.linux.debian.13.preflight_influxdb3_core`` and
``os.linux.debian.13.install_influxdb3_core`` apply the same posture to hosts that
already exist.

The verbatim cloud-config source of truth is tracked at
``netbox_packer/seeds/influxdb-core-3.11.0-debian-13.cloud-config.yaml``; the
constant below must stay byte-identical to it (asserted by
``tests/test_cloud_config_build_static.py``).

Seeding is deliberately collision-guarded rather than an overwrite: a pre-existing
row with different values aborts the migration and is left untouched, so operator
edits are never silently discarded. The reverse is a no-op because an operator may
already have baked the VMID.
"""

import hashlib

from django.db import migrations

INFLUXDB3_CORE_DEBIAN13_CLOUD_CONFIG = r"""#cloud-config
# InfluxDB 3 Core 3.11.0 on Debian 13 (Trixie), baked with the production posture
# of the operator installer rather than package defaults:
#
#   - binds only to 127.0.0.1:8181, with token authentication left enabled
#   - telemetry upload disabled
#   - Python Processing Engine disabled (no plugin-dir)
#   - explicit managed configuration file plus a systemd drop-in
#   - package held after install, for controlled upgrades
#
# Credential-free by design. No administrative token, TLS material, or per-bake
# state is written into this image: the first admin token is created and vaulted
# only by typed NMS RPC (service.influxdb.1.bootstrap, family=core3), which
# returns an nms-secret reference and never plaintext. The audited procedures
# os.linux.debian.13.preflight_influxdb3_core and
# os.linux.debian.13.install_influxdb3_core apply the same posture to hosts that
# already exist; this template is for new guests.
#
# node-id is derived from each clone's own hostname at first boot, so cloning the
# template never produces two nodes claiming the same identity.
package_update: false
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - gnupg
write_files:
  - path: /usr/local/sbin/install-influxdb3-core
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euo pipefail
      umask 027
      export DEBIAN_FRONTEND=noninteractive

      readonly PRODUCT_VERSION='3.11.0'
      readonly PACKAGE_NAME='influxdb3-core'
      readonly SERVICE_NAME='influxdb3-core.service'
      readonly EXPECTED_KEY_FINGERPRINT='24C975CBA61A024EE1B631787C3D57159FC2F927'
      readonly KEYRING_FILE='/etc/apt/keyrings/influxdata-archive.gpg'
      readonly SOURCE_FILE='/etc/apt/sources.list.d/influxdata.list'
      readonly CONFIG_DIR='/etc/influxdb3'
      readonly CONFIG_FILE='/etc/influxdb3/influxdb3-core.conf'
      readonly DATA_DIR='/var/lib/influxdb3/data'
      readonly HTTP_BIND='127.0.0.1:8181'
      readonly LOG_FILTER='info'
      readonly WAL_FLUSH_INTERVAL='100ms'
      readonly DROPIN_DIR='/etc/systemd/system/influxdb3-core.service.d'
      readonly DROPIN_FILE='/etc/systemd/system/influxdb3-core.service.d/20-production.conf'
      readonly MANAGED_MARKER='# Managed by netbox-packer influxdb-core-3.11.0-debian-13'

      # Debian 13 only. The apt suite, the packaged unit's sandbox, and the
      # configuration keys below are what this image was built and verified
      # against; refuse rather than half-configure another release.
      test -r /etc/os-release
      # shellcheck disable=SC1091
      . /etc/os-release
      if [ "${ID:-}" != 'debian' ]; then
        echo "This image supports Debian only; found ID=${ID:-unknown}" >&2
        exit 1
      fi
      case "${VERSION_ID:-}" in
        13|13.*) ;;
        *)
          echo "This image requires Debian 13; found VERSION_ID=${VERSION_ID:-unknown}" >&2
          exit 1
          ;;
      esac

      architecture="$(dpkg --print-architecture)"
      case "${architecture}" in
        amd64|arm64) ;;
        *)
          echo "Unsupported architecture: ${architecture}" >&2
          exit 1
          ;;
      esac

      if [ ! -d /run/systemd/system ]; then
        echo 'systemd is not running as PID 1' >&2
        exit 1
      fi

      install -d -m 0755 /etc/apt/keyrings

      # Verify the repository signing key by fingerprint before trusting it.
      curl --fail --silent --show-error --location \
        --proto '=https' --tlsv1.2 --retry 3 --retry-delay 2 \
        --output /tmp/influxdata-archive.key \
        https://repos.influxdata.com/influxdata-archive.key
      gpg --show-keys --with-fingerprint --with-colons /tmp/influxdata-archive.key 2>&1 \
        | grep -q "^fpr:\+${EXPECTED_KEY_FINGERPRINT}:$"
      gpg --dearmor < /tmp/influxdata-archive.key > "${KEYRING_FILE}"
      rm -f /tmp/influxdata-archive.key
      chmod 0644 "${KEYRING_FILE}"
      echo "deb [signed-by=${KEYRING_FILE}] https://repos.influxdata.com/debian stable main" \
        > "${SOURCE_FILE}"

      apt-get update

      # Pin to the requested semantic patch version, then verify what apt
      # actually installed before holding it.
      package_version="$(apt-cache madison "${PACKAGE_NAME}" | awk '
        {
          candidate=$3
          normalized=candidate
          sub(/^[0-9]+:/, "", normalized)
          if (normalized ~ /^3[.]11[.]0([+~-]|$)/) { print candidate; exit }
        }')"
      test -n "${package_version}"
      apt-get install -y --no-install-recommends "${PACKAGE_NAME}=${package_version}"
      installed="$(dpkg-query -W -f='${Version}' "${PACKAGE_NAME}")"
      normalized="${installed#*:}"
      case "${normalized}" in
        3.11.0|3.11.0[-+~]*) ;;
        *)
          echo "Unexpected ${PACKAGE_NAME} version: ${installed}" >&2
          exit 1
          ;;
      esac
      id influxdb3 >/dev/null 2>&1

      install -d -o root -g influxdb3 -m 0750 "${CONFIG_DIR}"
      install -d -o influxdb3 -g influxdb3 -m 0750 /var/lib/influxdb3
      install -d -o influxdb3 -g influxdb3 -m 0750 "${DATA_DIR}"

      # node-id must be unique per clone, so derive it from this host's own name.
      node_id="$(hostname -s 2>/dev/null || echo primary)"
      node_id="$(printf '%s' "${node_id}" | tr '[:upper:]_' '[:lower:]-' | tr -cd 'a-z0-9-')"
      node_id="${node_id#-}"
      node_id="${node_id%-}"
      if [ -z "${node_id}" ]; then
        node_id='primary'
      fi

      install -d -o root -g root -m 0755 "${DROPIN_DIR}"
      cat > "${DROPIN_FILE}" <<'DROPIN'
      [Service]
      Restart=on-failure
      RestartSec=5s
      TimeoutStopSec=120s
      DROPIN
      chmod 0644 "${DROPIN_FILE}"

      # Explicit production configuration for the packaged systemd launcher.
      # plugin-dir is deliberately omitted: the Processing Engine stays off.
      umask 077
      cat > "${CONFIG_FILE}.tmp" <<CONFIG
      ${MANAGED_MARKER}
      object-store = "file"
      data-dir = "${DATA_DIR}"
      node-id = "${node_id}-node"
      http-bind = "${HTTP_BIND}"
      log-filter = "${LOG_FILTER}"
      wal-flush-interval = "${WAL_FLUSH_INTERVAL}"
      disable-telemetry-upload = true
      # plugin-dir intentionally omitted: Processing Engine disabled.
      CONFIG
      umask 027
      chown root:influxdb3 "${CONFIG_FILE}.tmp"
      chmod 0640 "${CONFIG_FILE}.tmp"
      mv "${CONFIG_FILE}.tmp" "${CONFIG_FILE}"

      apt-mark hold "${PACKAGE_NAME}"

      systemctl daemon-reload
      systemctl enable --now "${SERVICE_NAME}"

      # /ready is the unauthenticated readiness endpoint; token authentication
      # stays enabled for every data and admin route.
      for _attempt in $(seq 1 60); do
        if curl --fail --silent "http://${HTTP_BIND}/ready" >/dev/null; then
          exit 0
        fi
        sleep 2
      done
      echo 'InfluxDB 3 Core readiness endpoint did not become ready' >&2
      systemctl status "${SERVICE_NAME}" --no-pager >&2 || true
      journalctl --unit "${SERVICE_NAME}" -n 120 --no-pager >&2 || true
      exit 1
runcmd:
  - [bash, /usr/local/sbin/install-influxdb3-core]
"""

CONFIG_NAME = "influxdb-core-3.11.0-debian-13-cloud-config"
CONFIG_VERSION = "3.11.0"
TEMPLATE_NAME = "influxdb-core-3.11.0-debian-13"
# Endpoint-agnostic, like the 0020 InfluxDB profiles: build dispatch supplies the
# proxbox-api endpoint_id and target_node.
PROXMOX_ENDPOINT = ""
PROXMOX_NODE = "select-at-build"
# Free against every VMID seeded to date (9010-9014, 9017-9019, 9032, 9040-9042,
# 9050, 9051, 9060, 9070, 9300) and adjacent to the InfluxDB family (9050 OSS 2,
# 9051 Core 3 on Ubuntu). Confirm it is still unused on the destination cluster
# before baking; supersede a bad artifact with a new VMID rather than deleting one.
TEMPLATE_VMID = 9052


def seed_influxdb3_core_debian13(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config_defaults = {
        "os_family": "debian",
        "installer_type": "cloud_config",
        "content": INFLUXDB3_CORE_DEBIAN13_CLOUD_CONFIG,
        "checksum": hashlib.sha256(INFLUXDB3_CORE_DEBIAN13_CLOUD_CONFIG.encode()).hexdigest(),
        "description": (
            "InfluxDB 3 Core 3.11.0 on Debian 13 with the production posture: "
            "loopback-only bind with token authentication, telemetry upload "
            "disabled, Processing Engine disabled, managed configuration plus "
            "systemd drop-in, and a held package. No credential is baked; the "
            "first admin token is vaulted by typed NMS RPC."
        ),
    }
    config, config_created = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults=config_defaults,
    )
    if not config_created:
        mismatches = [field for field, expected in config_defaults.items() if getattr(config, field) != expected]
        if mismatches:
            raise RuntimeError(
                f"InfluxDB 3 Core Debian 13 seed naming collision: installer config "
                f"{CONFIG_NAME!r} version {CONFIG_VERSION!r} already exists with "
                f"different values for {', '.join(mismatches)}. Rename the existing "
                "row, or delete it if it is genuinely obsolete, then rerun the "
                "migration. No existing row was modified."
            )

    template_defaults = {
        "os_family": "debian",
        "os_version": "13",
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
        "install_nms_agent": True,
        "nms_agent_backend_url": "https://backend.nms.nmulti.cloud",
        "provisions_service": "influxdb3-core",
        "installer_config": config,
        "description": (
            "InfluxDB 3 Core 3.11.0 cloud-init template for Debian 13 (Trixie), "
            "VMID 9052. Endpoint-agnostic: build dispatch supplies endpoint_id and "
            "target_node. First boot installs the pinned package, writes the managed "
            "loopback-only configuration, holds the package, and waits on the local "
            "readiness endpoint. Tokens, databases, and config changes are managed "
            "only through typed NMS RPC."
        ),
    }
    template, template_created = PackerTemplate.objects.get_or_create(
        name=TEMPLATE_NAME,
        defaults=template_defaults,
    )
    if not template_created:
        template_expected_values = {
            field: expected for field, expected in template_defaults.items() if field != "installer_config"
        }
        template_expected_values["installer_config_id"] = config.pk
        mismatches = [
            field for field, expected in template_expected_values.items() if getattr(template, field) != expected
        ]
        if mismatches:
            raise RuntimeError(
                f"InfluxDB 3 Core Debian 13 seed naming collision: template "
                f"{TEMPLATE_NAME!r} already exists with different values for "
                f"{', '.join(mismatches)}. Rename the existing row, or delete it if "
                "it is genuinely obsolete, then rerun the migration. No existing row "
                "was modified."
            )


def unseed_influxdb3_core_debian13(apps, schema_editor):
    # Golden-template seed rollbacks are intentionally non-destructive: an operator
    # may already have baked VMID 9052 or edited the database rows.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0024_seed_akvorado_cloud_init"),
    ]

    operations = [
        migrations.RunPython(
            seed_influxdb3_core_debian13,
            unseed_influxdb3_core_debian13,
        ),
    ]
