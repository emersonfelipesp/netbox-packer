"""Seed credential-free, version-pinned InfluxDB OSS 2 and Core 3 profiles."""

import hashlib

from django.db import migrations

INFLUXDB_OSS2_CLOUD_CONFIG = r"""#cloud-config
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


INFLUXDB_CORE3_CLOUD_CONFIG = r"""#cloud-config
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


PROFILES = (
    {
        "config_name": "influxdb-oss-2.9.1-ubuntu-2404-cloud-config",
        "config_version": "2.9.1",
        "content": INFLUXDB_OSS2_CLOUD_CONFIG,
        "template_name": "influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics",
        "vmid": 9050,
        "description": (
            "Endpoint-agnostic InfluxDB OSS 2.9.1 profile for Proxmox metrics and Flux. "
            "Build dispatch supplies proxbox-api endpoint_id and target_node; setup and "
            "credentials are managed only by typed NMS RPC."
        ),
    },
    {
        "config_name": "influxdb-core-3.11.0-ubuntu-2404-cloud-config",
        "config_version": "3.11.0",
        "content": INFLUXDB_CORE3_CLOUD_CONFIG,
        "template_name": "influxdb-core-3.11.0-ubuntu-2404",
        "vmid": 9051,
        "description": (
            "Endpoint-agnostic InfluxDB 3 Core 3.11.0 profile for general-purpose SQL, "
            "InfluxQL, and processing-engine workloads. Build dispatch supplies proxbox-api "
            "endpoint_id and target_node; tokens are managed only by typed NMS RPC."
        ),
    },
)


def _config_defaults(profile):
    return {
        "os_family": "ubuntu",
        "installer_type": "cloud_config",
        "content": profile["content"],
        "checksum": hashlib.sha256(profile["content"].encode()).hexdigest(),
        "description": profile["description"],
    }


def seed_influxdb_profiles(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    for profile in PROFILES:
        config, _ = PackerInstallerConfig.objects.update_or_create(
            name=profile["config_name"],
            version=profile["config_version"],
            defaults=_config_defaults(profile),
        )
        PackerTemplate.objects.update_or_create(
            name=profile["template_name"],
            defaults={
                "os_family": "ubuntu",
                "os_version": "24.04",
                "proxmox_template_id": profile["vmid"],
                "proxmox_endpoint": "",
                "proxmox_node": "select-at-build",
                "storage_pool": "local",
                "cloud_init_ready": True,
                "build_status": "pending",
                "packer_template_ref": "",
                "max_age_days": 30,
                "auto_rebuild": False,
                "installer_config": config,
                "installer_config_checksum_at_build": "",
                "install_qemu_guest_agent": True,
                "install_zabbix_agent2": True,
                "zabbix_server": "zabbix.nmulti.cloud",
                "description": profile["description"],
            },
        )

    # Existing installations may still have the credential-generating 0007
    # content in the database. Replace it in place and force a non-destructive
    # rebuild; the old Proxmox artifact is not deleted automatically.
    legacy = PackerInstallerConfig.objects.filter(
        name="influxdb-2-ubuntu-2404-proxmox-collector",
        version="1.0.0",
    ).first()
    if legacy is not None:
        legacy.content = INFLUXDB_OSS2_CLOUD_CONFIG
        legacy.checksum = hashlib.sha256(INFLUXDB_OSS2_CLOUD_CONFIG.encode()).hexdigest()
        legacy.description = (
            "Legacy InfluxDB collector profile hardened to install OSS 2.9.1 without "
            "credentials; use the typed RPC onboarding flow after cloning."
        )
        legacy.save(update_fields=["content", "checksum", "description"])
        PackerTemplate.objects.filter(
            name="influxdb-2-ubuntu-2404-proxmox-collector"
        ).update(
            build_status="pending",
            built_at=None,
            installer_config_checksum_at_build="",
            description=(
                "Legacy development-cluster InfluxDB collector profile. Its cloud-config is "
                "credential-free and pinned to OSS 2.9.1; prefer the endpoint-agnostic profile."
            ),
        )


def unseed_influxdb_profiles(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")
    PackerTemplate.objects.filter(
        name__in=[profile["template_name"] for profile in PROFILES]
    ).delete()
    for profile in PROFILES:
        PackerInstallerConfig.objects.filter(
            name=profile["config_name"],
            version=profile["config_version"],
        ).delete()


class Migration(migrations.Migration):
    dependencies = [("netbox_packer", "0019_stamp_fileserver_golden_template")]
    operations = [migrations.RunPython(seed_influxdb_profiles, unseed_influxdb_profiles)]
