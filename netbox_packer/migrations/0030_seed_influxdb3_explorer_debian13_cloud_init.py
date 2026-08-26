"""Seed the InfluxDB 3 Explorer 1.9.0 Debian 13 cloud-init template.

Explorer runs independently from InfluxDB 3 Core so each product has its own
template and build run. The container image is the reviewed public
``influxdata/influxdb3-ui`` multi-architecture manifest pinned by digest. Docker
comes from Debian's own signed repository, and one systemd unit owns the
container lifecycle with a loopback-default published address.

The image stays credential-free. Typed NMS RPC mints and vaults the Core token,
returns only an ``nms-secret:<opaque-id>`` reference, and provision-time
automation resolves it when writing the cloned guest's root-owned Explorer
connection configuration. No token, Core URL, password, private key, or
environment-specific secret reference is present in the golden image.

The verbatim cloud-config source of truth is tracked at
``netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml``; the
constant below must stay byte-identical to it.

Seeding is collision-guarded rather than an overwrite. A pre-existing row with
different values aborts the migration and remains untouched. Reverse is a no-op
because an operator may already have baked VMID 9053.
"""

import hashlib

from django.db import migrations

INFLUXDB3_EXPLORER_DEBIAN13_CLOUD_CONFIG = r"""#cloud-config
# InfluxDB 3 Explorer 1.9.0 on Debian 13 (Trixie).
#
# The public Explorer image is immutable by construction: every pull and run uses
# influxdata/influxdb3-ui at the reviewed multi-architecture manifest digest. The
# container publishes only its HTTP port, on loopback by default. Operators may set
# a different specific host IP in /etc/default/influxdb3-explorer before restarting
# the unit; never expose Explorer without an explicit access-control design because
# anyone who can reach it inherits the configured InfluxDB token's permissions.
#
# Credential-free by design. This golden image contains no InfluxDB Core URL, token,
# password, TLS private key, or Explorer session secret. After cloning, typed NMS RPC
# service.influxdb.1.token_create mints and vaults the Core token and returns only an
# nms-secret:<opaque-id> reference. Provision-time automation resolves that reference
# in memory, writes the root-owned, Explorer-group-readable connection under
# /etc/influxdb3-explorer, and restarts influxdb3-explorer.service. The reference and
# its resolved value are both per-instance state and must never be baked here.
#
# Zabbix Agent 2 and the NMS host agent are deliberately NOT injected into this
# template (install_zabbix_agent2 / install_nms_agent are False on the seeded
# PackerTemplate): the shared injectors build an Ubuntu Zabbix repository package
# name from VERSION_ID, which yields a nonexistent "ubuntu13" package on Debian 13,
# and the NMS agent bootstrap accepts only amd64. This installer is therefore the
# LAST runcmd entry. Cloud-init shellifies runcmd into a plain /bin/sh script with no
# `set -e`, so a non-final failure could otherwise be masked by a later success.
package_update: false
package_upgrade: false
packages: []
write_files:
  - path: /etc/default/influxdb3-explorer
    permissions: "0644"
    owner: root:root
    content: |
      # Bind to one host IP. Loopback is the safe default because Explorer does not
      # provide an independent user-authentication boundary for a configured token.
      EXPLORER_HOST_BIND=127.0.0.1
  - path: /usr/local/sbin/run-influxdb3-explorer
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -Eeuo pipefail

      readonly EXPLORER_IMAGE_REPOSITORY='influxdata/influxdb3-ui'
      readonly EXPLORER_IMAGE_DIGEST='sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc'
      readonly EXPLORER_IMAGE="${EXPLORER_IMAGE_REPOSITORY}@${EXPLORER_IMAGE_DIGEST}"
      readonly CONTAINER_NAME='influxdb3-explorer'
      readonly CONTAINER_PORT='8080'
      host_bind="${EXPLORER_HOST_BIND:-127.0.0.1}"

      # Accept only an IP-address-shaped value from the root-owned environment
      # file. Docker performs the definitive address validation. Quoting keeps the
      # value one argument and prevents shell interpretation.
      case "${host_bind}" in
        ''|*[!0-9A-Fa-f:.]*)
          echo "Invalid EXPLORER_HOST_BIND: ${host_bind}" >&2
          exit 1
          ;;
      esac
      if [[ "${host_bind}" == *:* ]]; then
        publish_address="[${host_bind}]:8080:${CONTAINER_PORT}/tcp"
      else
        publish_address="${host_bind}:8080:${CONTAINER_PORT}/tcp"
      fi

      exec /usr/bin/docker run \
        --name "${CONTAINER_NAME}" \
        --pull=never \
        --publish "${publish_address}" \
        --volume /var/lib/influxdb3-explorer:/db:rw \
        --volume /etc/influxdb3-explorer:/app-root/config:ro \
        "${EXPLORER_IMAGE}" \
        --mode=admin
  - path: /etc/systemd/system/influxdb3-explorer.service
    permissions: "0644"
    owner: root:root
    content: |
      [Unit]
      Description=InfluxDB 3 Explorer
      Requires=docker.service
      Wants=network-online.target
      After=docker.service network-online.target

      [Service]
      Type=simple
      EnvironmentFile=/etc/default/influxdb3-explorer
      ExecStartPre=-/usr/bin/docker rm --force influxdb3-explorer
      ExecStart=/usr/local/sbin/run-influxdb3-explorer
      ExecStop=-/usr/bin/docker stop --time 30 influxdb3-explorer
      ExecStopPost=-/usr/bin/docker rm --force influxdb3-explorer
      Restart=on-failure
      RestartSec=5s
      TimeoutStartSec=700
      TimeoutStopSec=60

      [Install]
      WantedBy=multi-user.target
  - path: /usr/share/doc/netbox-packer/influxdb3-explorer-provisioning.txt
    permissions: "0644"
    owner: root:root
    content: |
      This image intentionally has no configured InfluxDB connection.

      After cloning, service.influxdb.1.token_create returns an opaque
      nms-secret:<opaque-id> reference, never plaintext. Provision-time automation
      resolves that reference only in memory, writes the per-instance Explorer
      connection configuration to /etc/influxdb3-explorer/config.json with mode
      0640 with owner root:1500, then restarts influxdb3-explorer.service.

      Never write the secret reference or its resolved value into a golden image.
  - path: /usr/local/sbin/install-influxdb3-explorer
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -Eeuo pipefail
      umask 027
      export DEBIAN_FRONTEND=noninteractive

      readonly EXPLORER_IMAGE_REPOSITORY='influxdata/influxdb3-ui'
      readonly EXPLORER_IMAGE_DIGEST='sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc'
      readonly EXPLORER_IMAGE="${EXPLORER_IMAGE_REPOSITORY}@${EXPLORER_IMAGE_DIGEST}"
      readonly EXPLORER_UID='1500'
      readonly EXPLORER_GID='1500'
      readonly NMS_FAILURE_MARKER='/var/lib/nms/influxdb-install-failed'
      readonly MAX_IMAGE_SIZE_BYTES='2147483648'

      # Cloud-init's runcmd wrapper has no `set -e`. Record durable evidence for
      # every failure path instead of trusting only the wrapper's final status. An
      # EXIT trap is required rather than ERR because explicit `exit 1` paths do
      # not run an ERR trap.
      on_install_exit() {
        local exit_code=$?
        if [ "${exit_code}" -ne 0 ]; then
          install -d -m 0755 /var/lib/nms || true
          {
            printf 'installer: %s\n' "$0"
            printf 'exit_code: %s\n' "${exit_code}"
            printf 'failed_at: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
          } > "${NMS_FAILURE_MARKER}" || true
        fi
        return "${exit_code}"
      }
      # Convert fatal signals into non-zero exits before installing the EXIT trap.
      # Otherwise bash may preserve a previous successful status and hide a systemd
      # cancellation, guest shutdown, or external timeout.
      trap 'exit 143' TERM
      trap 'exit 130' INT
      trap 'exit 129' HUP
      trap on_install_exit EXIT

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

      # Docker comes only from Debian's signed repository. apt has bounded
      # connection attempts and retries, while timeout supplies an overall
      # deadline even if a subprocess becomes silent.
      readonly -a APT_BOUNDS=(
        -o Acquire::Retries=3
        -o Acquire::http::Timeout=30
        -o Acquire::https::Timeout=30
      )
      timeout --signal=TERM --kill-after=30s 600s \
        apt-get "${APT_BOUNDS[@]}" update
      timeout --signal=TERM --kill-after=30s 600s \
        apt-get "${APT_BOUNDS[@]}" install -y --no-install-recommends \
          ca-certificates curl docker.io

      # Explorer 1.9.0 runs as non-root uid/gid 1500. Keep provisioned
      # configuration root-owned and group-readable, and give the container
      # exclusive ownership of its writable SQLite directory.
      install -d -o root -g "${EXPLORER_GID}" -m 0750 /etc/influxdb3-explorer
      install -d -o "${EXPLORER_UID}" -g "${EXPLORER_GID}" -m 0700 \
        /var/lib/influxdb3-explorer

      systemctl enable --now docker.service

      # The OCI reference is digest-only. Bound the pull even if the registry or
      # daemon stalls, then reject an unexpectedly large unpacked image before it
      # can become the service runtime.
      timeout --signal=TERM --kill-after=30s 600s \
        /usr/bin/docker pull "${EXPLORER_IMAGE}"
      pulled_size="$(/usr/bin/docker image inspect \
        --format '{{.Size}}' "${EXPLORER_IMAGE}")"
      case "${pulled_size}" in
        ''|*[!0-9]*)
          echo "Docker returned an invalid Explorer image size: ${pulled_size}" >&2
          exit 1
          ;;
      esac
      if [ "${pulled_size}" -gt "${MAX_IMAGE_SIZE_BYTES}" ]; then
        /usr/bin/docker image rm "${EXPLORER_IMAGE}" >/dev/null 2>&1 || true
        echo "Explorer image exceeds ${MAX_IMAGE_SIZE_BYTES} bytes" >&2
        exit 1
      fi
      /usr/bin/docker image inspect "${EXPLORER_IMAGE}" >/dev/null

      systemctl daemon-reload
      systemctl enable --now influxdb3-explorer.service

      # The local HTTP probe is individually bounded and the loop has an overall
      # monotonic deadline, so a socket that accepts but never answers cannot hang
      # first boot indefinitely.
      readiness_deadline=$((SECONDS + 180))
      while [ "${SECONDS}" -lt "${readiness_deadline}" ]; do
        if curl --fail --silent --show-error \
          --connect-timeout 2 --max-time 5 \
          http://127.0.0.1:8080/ >/dev/null; then
          exit 0
        fi
        sleep 2
      done
      echo 'InfluxDB 3 Explorer did not become ready on loopback port 8080' >&2
      systemctl status influxdb3-explorer.service --no-pager >&2 || true
      journalctl --unit influxdb3-explorer.service -n 120 --no-pager >&2 || true
      exit 1
runcmd:
  - [bash, /usr/local/sbin/install-influxdb3-explorer]
"""

CONFIG_NAME = "influxdb3-explorer-1.9.0-debian-13-cloud-config"
CONFIG_VERSION = "1.9.0"
TEMPLATE_NAME = "influxdb3-explorer-1.9.0-debian-13"
PROXMOX_ENDPOINT = ""
PROXMOX_NODE = "select-at-build"
# Free against the entire seeded catalog through migration 0029 and adjacent to
# the InfluxDB family (9050 OSS 2, 9051 Core 3 on Ubuntu, 9052 Core 3 on Debian).
# Confirm it remains unused on the selected destination before baking.
TEMPLATE_VMID = 9053
_MUTABLE_BUILD_STATE_FIELDS = frozenset(
    {
        "installer_config",
        "build_status",
        "packer_template_ref",
    }
)


def seed_influxdb3_explorer_debian13(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config_defaults = {
        "os_family": "debian",
        "installer_type": "cloud_config",
        "content": INFLUXDB3_EXPLORER_DEBIAN13_CLOUD_CONFIG,
        "checksum": hashlib.sha256(INFLUXDB3_EXPLORER_DEBIAN13_CLOUD_CONFIG.encode()).hexdigest(),
        "description": (
            "InfluxDB 3 Explorer 1.9.0 on Debian 13 using the digest-pinned "
            "influxdata/influxdb3-ui image. Docker comes from Debian; the "
            "systemd-owned container is loopback-only by default. Core "
            "connection state and credentials arrive only after cloning."
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
                f"InfluxDB 3 Explorer Debian 13 seed naming collision: installer "
                f"config {CONFIG_NAME!r} version {CONFIG_VERSION!r} already exists "
                f"with different values for {', '.join(mismatches)}. Rename the "
                "existing row, or delete it if it is genuinely obsolete, then "
                "rerun the migration. No existing row was modified."
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
        # Both optional injectors are incompatible with this Debian/arm64-capable
        # profile. Keeping them off also keeps the hardened installer last in runcmd.
        "install_zabbix_agent2": False,
        "install_nms_agent": False,
        "provisions_service": "influxdb3-explorer",
        "installer_config": config,
        "description": (
            "InfluxDB 3 Explorer 1.9.0 cloud-init template for Debian 13 "
            "(Trixie), VMID 9053. Endpoint-agnostic: build dispatch selects an "
            "authorized enabled PackerBuildTarget URL and target_node. The Explorer "
            "container is pinned by digest and binds to loopback by default; typed NMS "
            "provisioning supplies the vaulted Core connection after cloning."
        ),
    }
    template, template_created = PackerTemplate.objects.get_or_create(
        name=TEMPLATE_NAME,
        defaults=template_defaults,
    )
    if not template_created:
        template_expected_values = {
            field: expected for field, expected in template_defaults.items() if field not in _MUTABLE_BUILD_STATE_FIELDS
        }
        template_expected_values["installer_config_id"] = config.pk
        mismatches = [
            field for field, expected in template_expected_values.items() if getattr(template, field) != expected
        ]
        if mismatches:
            raise RuntimeError(
                f"InfluxDB 3 Explorer Debian 13 seed naming collision: template "
                f"{TEMPLATE_NAME!r} already exists with different values for "
                f"{', '.join(mismatches)}. Rename the existing row, or delete it "
                "if it is genuinely obsolete, then rerun the migration. No "
                "existing row was modified."
            )


def unseed_influxdb3_explorer_debian13(apps, schema_editor):
    # Golden-template seed rollbacks are intentionally non-destructive: an operator
    # may already have baked VMID 9053 or edited the database rows.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0029_pin_influxdb3_debian13_base_image"),
    ]

    operations = [
        migrations.RunPython(
            seed_influxdb3_explorer_debian13,
            unseed_influxdb3_explorer_debian13,
        ),
    ]
