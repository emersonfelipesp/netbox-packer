"""Harden the two InfluxDB profiles seeded by migration ``0020``.

Migration ``0025`` fixed three defects in the Debian 13 InfluxDB 3 Core profile that
adversarial review surfaced. The two ``0020`` profiles
(``influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics`` and
``influxdb-core-3.11.0-ubuntu-2404``) share the same shell shape and therefore the
same three defects. This migration brings them to parity.

1. **The keyring trust boundary admitted extra keys (security).** The old script
   proved only that the downloaded file *contained* the expected fingerprint, then
   dearmored the **whole file** into ``/etc/apt/keyrings``. A substituted file
   carrying the genuine key **plus** an attacker key passes that ``grep``; APT is
   then configured to trust the resulting keyring without being constrained to the
   expected fingerprint, so the attacker key can sign repository metadata and obtain
   root during ``apt-get install``. The hardened script imports into an isolated
   ``GNUPGHOME``, exports only the expected primary fingerprint
   (``--export-options export-minimal``, which preserves the signing subkeys APT
   needs), and asserts the exported keyring holds exactly one ``pub`` record with
   that fingerprint.
2. **Prereleases were accepted as the pinned release.** A tilde sorts *before* the
   release it qualifies, so ``2.9.1~rc1`` / ``3.11.0~rc1`` is a prerelease, and both
   the candidate regex and the post-install check accepted it — the script could
   silently install **and hold** an unreviewed vendor build while reporting success.
   Only a final version, optionally with a Debian revision, is now accepted, and any
   ``~`` version is refused explicitly.
3. **Downloads and the readiness loop were unbounded.** ``--retry`` does not help
   against a server that completes TLS and then stops sending data: no error is
   raised, so nothing triggers a retry and the first-boot script blocks forever,
   leaving the clone stuck in cloud-init. Every download now carries connection,
   per-attempt, and overall retry deadlines plus a response-size cap, and the
   readiness loop bounds each probe and enforces an overall deadline.

**A row that no longer matches the exact ``0020`` baseline is never silently
skipped.** It is not rewritten either — that would discard an operator's edit — so
the migration instead **fails with the offending rows named**, because a silent skip
would let an operator deploy this believing the root-compromise vector was removed
everywhere while the untouched row keeps it. Resolve such a row by hand (or delete it
if obsolete) and rerun.

Rebake invalidation follows ``installer_config_id``, not the canonical template name:
names are editable and several templates can share one config, so a renamed or
additional consumer would otherwise keep ``ready`` state and its pre-hardening
artifact. Any linked template whose recorded build checksum differs from the hardened
checksum is marked ``pending``, including when the content was already hardened by
hand but the *artifact* was baked from the legacy content. The migration also refuses
to run while a build is queued or running against a linked template, since that build
read the old content and would finish by writing ``ready`` over the rebake marker.

Per the estate guardrail, existing Proxmox artifacts are never deleted — supersede a
bad artifact by baking a new VMID.

The verbatim hardened sources are tracked at
``netbox_packer/seeds/influxdb-oss-2.9.1-ubuntu-2404.cloud-config.yaml`` and
``netbox_packer/seeds/influxdb-core-3.11.0-ubuntu-2404.cloud-config.yaml``; the
constants below must stay byte-identical to them (asserted by
``tests/test_cloud_config_build_static.py``).
"""

import hashlib

from django.db import migrations

# Verbatim content as seeded by migration 0020. Used only as an equality guard, so a
# customised row is never overwritten.
LEGACY_OSS2_CLOUD_CONFIG = r"""#cloud-config
# InfluxDB OSS 2.9.1 for Proxmox metrics/Flux workloads.
# Credentials and initial setup are intentionally deferred to typed NMS RPC.
package_update: false
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - gnupg
write_files:
  - path: /opt/nms-influxdb-install.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      readonly PRODUCT_VERSION='2.9.1'
      install -d -m 0755 /etc/apt/keyrings /etc/influxdb/nms-managed
      curl --fail --silent --show-error --location \
        --output /tmp/influxdata-archive.key \
        https://repos.influxdata.com/influxdata-archive.key
      gpg --show-keys --with-fingerprint --with-colons /tmp/influxdata-archive.key 2>&1 \
        | grep -q '^fpr:\+24C975CBA61A024EE1B631787C3D57159FC2F927:$'
      gpg --dearmor < /tmp/influxdata-archive.key \
        > /etc/apt/keyrings/influxdata-archive.gpg
      rm -f /tmp/influxdata-archive.key
      echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \
        > /etc/apt/sources.list.d/influxdata.list
      apt-get update
      package_version="$(apt-cache madison influxdb2 | awk '
        {
          candidate=$3
          normalized=candidate
          sub(/^[0-9]+:/, "", normalized)
          if (normalized ~ /^2[.]9[.]1([+~-]|$)/) { print candidate; exit }
        }')"
      test -n "${package_version}"
      apt-get install -y --no-install-recommends "influxdb2=${package_version}"
      installed="$(dpkg-query -W -f='${Version}' influxdb2)"
      normalized="${installed#*:}"
      case "${normalized}" in
        2.9.1|2.9.1[-+~]*) ;;
        *) echo "Unexpected influxdb2 version: ${installed}" >&2; exit 1 ;;
      esac
      apt-mark hold influxdb2
      systemctl enable --now influxdb.service
      for attempt in $(seq 1 60); do
        curl --fail --silent http://127.0.0.1:8086/health >/dev/null && exit 0
        sleep 2
      done
      echo 'InfluxDB OSS health endpoint did not become ready' >&2
      exit 1
runcmd:
  - [bash, /opt/nms-influxdb-install.sh]
  - [rm, -f, /opt/nms-influxdb-install.sh]
"""

LEGACY_CORE3_CLOUD_CONFIG = r"""#cloud-config
# InfluxDB 3 Core 3.11.0 for general-purpose SQL/InfluxQL workloads.
# The first operator token is intentionally created and vaulted by typed NMS RPC.
package_update: false
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - gnupg
write_files:
  - path: /opt/nms-influxdb3-install.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      readonly PRODUCT_VERSION='3.11.0'
      install -d -m 0755 \
        /etc/apt/keyrings \
        /etc/influxdb3/nms-managed \
        /var/lib/influxdb3/plugins
      curl --fail --silent --show-error --location \
        --output /tmp/influxdata-archive.key \
        https://repos.influxdata.com/influxdata-archive.key
      gpg --show-keys --with-fingerprint --with-colons /tmp/influxdata-archive.key 2>&1 \
        | grep -q '^fpr:\+24C975CBA61A024EE1B631787C3D57159FC2F927:$'
      gpg --dearmor < /tmp/influxdata-archive.key \
        > /etc/apt/keyrings/influxdata-archive.gpg
      rm -f /tmp/influxdata-archive.key
      echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \
        > /etc/apt/sources.list.d/influxdata.list
      apt-get update
      package_version="$(apt-cache madison influxdb3-core | awk '
        {
          candidate=$3
          normalized=candidate
          sub(/^[0-9]+:/, "", normalized)
          if (normalized ~ /^3[.]11[.]0([+~-]|$)/) { print candidate; exit }
        }')"
      test -n "${package_version}"
      apt-get install -y --no-install-recommends "influxdb3-core=${package_version}"
      installed="$(dpkg-query -W -f='${Version}' influxdb3-core)"
      normalized="${installed#*:}"
      case "${normalized}" in
        3.11.0|3.11.0[-+~]*) ;;
        *) echo "Unexpected influxdb3-core version: ${installed}" >&2; exit 1 ;;
      esac
      apt-mark hold influxdb3-core
      systemctl enable --now influxdb3-core.service
      for attempt in $(seq 1 60); do
        curl --fail --silent http://127.0.0.1:8181/ready >/dev/null && exit 0
        sleep 2
      done
      echo 'InfluxDB 3 Core readiness endpoint did not become ready' >&2
      exit 1
runcmd:
  - [bash, /opt/nms-influxdb3-install.sh]
  - [rm, -f, /opt/nms-influxdb3-install.sh]
"""

HARDENED_OSS2_CLOUD_CONFIG = r"""#cloud-config
# InfluxDB OSS 2.9.1 for Proxmox metrics/Flux workloads.
# Credentials and initial setup are intentionally deferred to typed NMS RPC.
package_update: false
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - gnupg
write_files:
  - path: /opt/nms-influxdb-install.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -Eeuo pipefail
      export DEBIAN_FRONTEND=noninteractive

      # Cloud-init shellifies runcmd into a plain /bin/sh script with no `set -e`, and
      # build-time injection appends the Zabbix bootstrap AFTER this installer, so a
      # non-zero exit here is masked by a later command's success and cloud-init still
      # reports success. Record a durable marker instead of relying on that exit
      # status, and never delete this script, so a failed guest keeps its evidence.
      # An EXIT trap, deliberately NOT an ERR trap: bash does not run an ERR trap for
      # an explicit `exit 1`, and this script rejects prereleases, unexpected installed
      # versions, and readiness timeouts with exactly that. An EXIT handler covers every
      # termination path, so no failure mode goes unrecorded. It also owns the temporary
      # keyring cleanup, since a script may install only one EXIT trap.
      readonly NMS_FAILURE_MARKER='/var/lib/nms/influxdb-install-failed'
      keyring_workdir=''
      on_install_exit() {
        local exit_code=$?
        if [ -n "${keyring_workdir}" ]; then
          rm -rf "${keyring_workdir}" || true
        fi
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
      trap on_install_exit EXIT
      readonly PRODUCT_VERSION='2.9.1'
      install -d -m 0755 /etc/apt/keyrings /etc/influxdb/nms-managed
      # Trust EXACTLY ONE key. Proving the downloaded file *contains* the expected
      # fingerprint and then dearmoring the whole file would also trust any extra
      # key bundled alongside it: a substituted file carrying the genuine key plus
      # an attacker key passes a `grep`, and that attacker key could then sign
      # repository metadata and gain root during install. Import into an isolated
      # keyring, export only the expected primary fingerprint, and prove the
      # exported keyring holds that one key and nothing else.
      #
      # The download is bounded on every axis: --retry alone does not help against
      # a server that completes TLS and then stops sending data, and an unbounded
      # response could fill /tmp before the key is ever filtered.
      readonly EXPECTED_KEY_FINGERPRINT='24C975CBA61A024EE1B631787C3D57159FC2F927'
      keyring_workdir="$(mktemp -d)"
      chmod 0700 "${keyring_workdir}"
      curl --fail --silent --show-error --location \
        --proto '=https' --tlsv1.2 \
        --retry 3 --retry-delay 2 --retry-max-time 60 \
        --connect-timeout 10 --max-time 30 --max-filesize 1048576 \
        --output "${keyring_workdir}/influxdata-archive.key" \
        https://repos.influxdata.com/influxdata-archive.key
      GNUPGHOME="${keyring_workdir}" gpg --batch --quiet \
        --import "${keyring_workdir}/influxdata-archive.key"
      GNUPGHOME="${keyring_workdir}" gpg --batch --yes \
        --export --export-options export-minimal \
        "${EXPECTED_KEY_FINGERPRINT}" > "${keyring_workdir}/influxdata-archive.gpg"
      test -s "${keyring_workdir}/influxdata-archive.gpg"
      exported_primaries="$(gpg --show-keys --with-colons \
        "${keyring_workdir}/influxdata-archive.gpg" | grep -c '^pub:' || true)"
      test "${exported_primaries}" = '1'
      gpg --show-keys --with-colons "${keyring_workdir}/influxdata-archive.gpg" \
        | grep -q "^fpr:::::::::${EXPECTED_KEY_FINGERPRINT}:$"
      install -o root -g root -m 0644 \
        "${keyring_workdir}/influxdata-archive.gpg" \
        /etc/apt/keyrings/influxdata-archive.gpg
      rm -rf "${keyring_workdir}"
      keyring_workdir=''
      echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \
        > /etc/apt/sources.list.d/influxdata.list
      apt-get update
      package_version="$(apt-cache madison influxdb2 | awk '
        {
          candidate=$3
          normalized=candidate
          sub(/^[0-9]+:/, "", normalized)
          if (normalized ~ /^2[.]9[.]1(-[0-9A-Za-z.+]+)?$/) { print candidate; exit }
        }')"
      test -n "${package_version}"
      apt-get install -y --no-install-recommends "influxdb2=${package_version}"
      installed="$(dpkg-query -W -f='${Version}' influxdb2)"
      normalized="${installed#*:}"
      case "${normalized}" in
        *'~'*)
          echo "Refusing prerelease influxdb2 version: ${installed}" >&2
          exit 1
          ;;
      esac
      case "${normalized}" in
        2.9.1|2.9.1-*) ;;
        *) echo "Unexpected influxdb2 version: ${installed}" >&2; exit 1 ;;
      esac
      apt-mark hold influxdb2
      systemctl enable --now influxdb.service
      # Bounded per probe AND overall: without --connect-timeout/--max-time a
      # service that accepts the connection but never answers blocks the first
      # curl forever, so the nominal attempt count is never reached and
      # once-per-instance cloud-init hangs after a partial install.
      readiness_deadline=$((SECONDS + 180))
      while [ "${SECONDS}" -lt "${readiness_deadline}" ]; do
        if curl --fail --silent --connect-timeout 2 --max-time 5 \
          http://127.0.0.1:8086/health >/dev/null; then
          exit 0
        fi
        sleep 2
      done
      echo 'InfluxDB OSS health endpoint did not become ready' >&2
      exit 1
runcmd:
  - [bash, /opt/nms-influxdb-install.sh]
"""

HARDENED_CORE3_CLOUD_CONFIG = r"""#cloud-config
# InfluxDB 3 Core 3.11.0 for general-purpose SQL/InfluxQL workloads.
# The first operator token is intentionally created and vaulted by typed NMS RPC.
package_update: false
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - gnupg
write_files:
  - path: /opt/nms-influxdb3-install.sh
    permissions: "0755"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -Eeuo pipefail
      export DEBIAN_FRONTEND=noninteractive

      # Cloud-init shellifies runcmd into a plain /bin/sh script with no `set -e`, and
      # build-time injection appends the Zabbix bootstrap AFTER this installer, so a
      # non-zero exit here is masked by a later command's success and cloud-init still
      # reports success. Record a durable marker instead of relying on that exit
      # status, and never delete this script, so a failed guest keeps its evidence.
      # An EXIT trap, deliberately NOT an ERR trap: bash does not run an ERR trap for
      # an explicit `exit 1`, and this script rejects prereleases, unexpected installed
      # versions, and readiness timeouts with exactly that. An EXIT handler covers every
      # termination path, so no failure mode goes unrecorded. It also owns the temporary
      # keyring cleanup, since a script may install only one EXIT trap.
      readonly NMS_FAILURE_MARKER='/var/lib/nms/influxdb-install-failed'
      keyring_workdir=''
      on_install_exit() {
        local exit_code=$?
        if [ -n "${keyring_workdir}" ]; then
          rm -rf "${keyring_workdir}" || true
        fi
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
      trap on_install_exit EXIT
      readonly PRODUCT_VERSION='3.11.0'
      install -d -m 0755 \
        /etc/apt/keyrings \
        /etc/influxdb3/nms-managed \
        /var/lib/influxdb3/plugins
      # Trust EXACTLY ONE key. Proving the downloaded file *contains* the expected
      # fingerprint and then dearmoring the whole file would also trust any extra
      # key bundled alongside it: a substituted file carrying the genuine key plus
      # an attacker key passes a `grep`, and that attacker key could then sign
      # repository metadata and gain root during install. Import into an isolated
      # keyring, export only the expected primary fingerprint, and prove the
      # exported keyring holds that one key and nothing else.
      #
      # The download is bounded on every axis: --retry alone does not help against
      # a server that completes TLS and then stops sending data, and an unbounded
      # response could fill /tmp before the key is ever filtered.
      readonly EXPECTED_KEY_FINGERPRINT='24C975CBA61A024EE1B631787C3D57159FC2F927'
      keyring_workdir="$(mktemp -d)"
      chmod 0700 "${keyring_workdir}"
      curl --fail --silent --show-error --location \
        --proto '=https' --tlsv1.2 \
        --retry 3 --retry-delay 2 --retry-max-time 60 \
        --connect-timeout 10 --max-time 30 --max-filesize 1048576 \
        --output "${keyring_workdir}/influxdata-archive.key" \
        https://repos.influxdata.com/influxdata-archive.key
      GNUPGHOME="${keyring_workdir}" gpg --batch --quiet \
        --import "${keyring_workdir}/influxdata-archive.key"
      GNUPGHOME="${keyring_workdir}" gpg --batch --yes \
        --export --export-options export-minimal \
        "${EXPECTED_KEY_FINGERPRINT}" > "${keyring_workdir}/influxdata-archive.gpg"
      test -s "${keyring_workdir}/influxdata-archive.gpg"
      exported_primaries="$(gpg --show-keys --with-colons \
        "${keyring_workdir}/influxdata-archive.gpg" | grep -c '^pub:' || true)"
      test "${exported_primaries}" = '1'
      gpg --show-keys --with-colons "${keyring_workdir}/influxdata-archive.gpg" \
        | grep -q "^fpr:::::::::${EXPECTED_KEY_FINGERPRINT}:$"
      install -o root -g root -m 0644 \
        "${keyring_workdir}/influxdata-archive.gpg" \
        /etc/apt/keyrings/influxdata-archive.gpg
      rm -rf "${keyring_workdir}"
      keyring_workdir=''
      echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \
        > /etc/apt/sources.list.d/influxdata.list
      apt-get update
      package_version="$(apt-cache madison influxdb3-core | awk '
        {
          candidate=$3
          normalized=candidate
          sub(/^[0-9]+:/, "", normalized)
          if (normalized ~ /^3[.]11[.]0(-[0-9A-Za-z.+]+)?$/) { print candidate; exit }
        }')"
      test -n "${package_version}"
      apt-get install -y --no-install-recommends "influxdb3-core=${package_version}"
      installed="$(dpkg-query -W -f='${Version}' influxdb3-core)"
      normalized="${installed#*:}"
      case "${normalized}" in
        *'~'*)
          echo "Refusing prerelease influxdb3-core version: ${installed}" >&2
          exit 1
          ;;
      esac
      case "${normalized}" in
        3.11.0|3.11.0-*) ;;
        *) echo "Unexpected influxdb3-core version: ${installed}" >&2; exit 1 ;;
      esac
      apt-mark hold influxdb3-core
      systemctl enable --now influxdb3-core.service
      # Bounded per probe AND overall: without --connect-timeout/--max-time a
      # service that accepts the connection but never answers blocks the first
      # curl forever, so the nominal attempt count is never reached and
      # once-per-instance cloud-init hangs after a partial install.
      readiness_deadline=$((SECONDS + 180))
      while [ "${SECONDS}" -lt "${readiness_deadline}" ]; do
        if curl --fail --silent --connect-timeout 2 --max-time 5 \
          http://127.0.0.1:8181/ready >/dev/null; then
          exit 0
        fi
        sleep 2
      done
      echo 'InfluxDB 3 Core readiness endpoint did not become ready' >&2
      exit 1
runcmd:
  - [bash, /opt/nms-influxdb3-install.sh]
"""

# (installer-config name, version, hardened content, legacy content, template names)
_PROFILES = (
    (
        "influxdb-oss-2.9.1-ubuntu-2404-cloud-config",
        "2.9.1",
        HARDENED_OSS2_CLOUD_CONFIG,
        LEGACY_OSS2_CLOUD_CONFIG,
        ("influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics",),
    ),
    (
        "influxdb-core-3.11.0-ubuntu-2404-cloud-config",
        "3.11.0",
        HARDENED_CORE3_CLOUD_CONFIG,
        LEGACY_CORE3_CLOUD_CONFIG,
        ("influxdb-core-3.11.0-ubuntu-2404",),
    ),
    # Migration 0020 also rewrote this legacy development row with the OSS 2 content,
    # so it carries the same three defects.
    (
        "influxdb-2-ubuntu-2404-proxmox-collector",
        "1.0.0",
        HARDENED_OSS2_CLOUD_CONFIG,
        LEGACY_OSS2_CLOUD_CONFIG,
        ("influxdb-2-ubuntu-2404-proxmox-collector",),
    ),
)

_HARDENED_DESCRIPTION_SUFFIX = (
    " Hardened: single-key repository trust, final-release-only version pin, and "
    "bounded downloads and readiness probes."
)


def harden_influxdb_0020_profiles(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")
    PackerBuild = apps.get_model("netbox_packer", "PackerBuild")

    unresolved = []

    for name, version, hardened, legacy, _template_names in _PROFILES:
        config = PackerInstallerConfig.objects.filter(name=name, version=version).first()
        if config is None:
            continue

        hardened_checksum = hashlib.sha256(hardened.encode()).hexdigest()
        linked = PackerTemplate.objects.filter(installer_config_id=config.pk)

        # A build in flight read the OLD installer content and will write `ready`
        # when it finishes, overwriting the rebake marker set below and leaving a
        # vulnerable artifact recorded as current. Refuse rather than race it.
        active = list(
            PackerBuild.objects.filter(
                template_id__in=list(linked.values_list("pk", flat=True)),
                status__in=("queued", "running"),
            ).values_list("template_id", flat=True)
        )
        if active:
            raise RuntimeError(
                f"Cannot harden installer config {name!r} version {version!r}: "
                f"{len(active)} build(s) are queued or running against its templates "
                "and would finish by baking the pre-hardening content and marking it "
                "ready. Wait for those builds to finish (or cancel them), then rerun "
                "the migration."
            )

        if config.content != hardened:
            if config.content != legacy:
                # Operator-modified content. Do NOT silently skip: the row may still
                # carry the keyring, prerelease, and unbounded-download defects, and a
                # silent skip lets an operator believe the fix was applied everywhere.
                unresolved.append(f"{name!r} version {version!r}")
                continue

            config.content = hardened
            config.checksum = hardened_checksum
            description = config.description or ""
            if _HARDENED_DESCRIPTION_SUFFIX.strip() not in description:
                config.description = (description + _HARDENED_DESCRIPTION_SUFFIX).strip()
            config.save(update_fields=["content", "checksum", "description"])

        # Invalidate by the real relationship, not by canonical name: template names
        # are editable and several templates can share one installer config, so a
        # renamed or additional consumer would otherwise keep `ready` state and its
        # pre-hardening artifact. Runs even when the content was already hardened,
        # because the *artifact* may still have been baked from the legacy content.
        # Nothing is deleted; supersede a bad artifact by baking a new VMID.
        linked.exclude(installer_config_checksum_at_build=hardened_checksum).update(
            build_status="pending",
            built_at=None,
            installer_config_checksum_at_build="",
        )

    if unresolved:
        raise RuntimeError(
            "Refusing to leave a known-vulnerable InfluxDB profile in place. These "
            "installer configs no longer match the baseline this migration knows how "
            "to replace, so they were not rewritten: "
            + ", ".join(unresolved)
            + ". Each may still contain the unrestricted keyring import, the "
            "prerelease-accepting version match, and unbounded downloads. Apply those "
            "three fixes to the row by hand (see "
            "docs/cloud-init-template-images.md), or delete the row if it is "
            "genuinely obsolete, then rerun this migration."
        )


def unharden_influxdb_0020_profiles(apps, schema_editor):
    # Intentionally a no-op: rolling back must not restore a keyring trust boundary
    # that accepts attacker-supplied keys, nor discard an operator's rebake state.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0025_seed_influxdb3_core_debian13_cloud_init"),
    ]

    operations = [
        migrations.RunPython(
            harden_influxdb_0020_profiles,
            unharden_influxdb_0020_profiles,
        ),
    ]
