"""Correct seeded template guidance for endpoint-authorized cloud builds.

The three endpoint-agnostic InfluxDB templates previously told operators to
supply a proxbox-api ``endpoint_id``. Caller-provided numeric ids are no longer
authorization: builds select an enabled ``PackerBuildTarget`` URL, resolve it to
one netbox-proxbox endpoint, and require both endpoint write gates.

Each update is an exact compare-and-set against the historical seeded text.
Missing, renamed, already-corrected, or operator-edited rows remain untouched.
Reverse is a no-op so a rollback never restores obsolete guidance.
"""

from django.db import migrations

DESCRIPTION_UPDATES = (
    (
        "influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics",
        (
            "Endpoint-agnostic InfluxDB OSS 2.9.1 profile for Proxmox metrics and Flux. "
            "Build dispatch supplies proxbox-api endpoint_id and target_node; setup and "
            "credentials are managed only by typed NMS RPC."
        ),
        (
            "Endpoint-agnostic InfluxDB OSS 2.9.1 profile for Proxmox metrics and Flux. "
            "Build dispatch selects an authorized enabled PackerBuildTarget URL and "
            "target_node; setup and credentials are managed only by typed NMS RPC."
        ),
    ),
    (
        "influxdb-core-3.11.0-ubuntu-2404",
        (
            "Endpoint-agnostic InfluxDB 3 Core 3.11.0 profile for general-purpose SQL, "
            "InfluxQL, and processing-engine workloads. Build dispatch supplies proxbox-api "
            "endpoint_id and target_node; tokens are managed only by typed NMS RPC."
        ),
        (
            "Endpoint-agnostic InfluxDB 3 Core 3.11.0 profile for general-purpose SQL, "
            "InfluxQL, and processing-engine workloads. Build dispatch selects an authorized "
            "enabled PackerBuildTarget URL and target_node; tokens are managed only by typed NMS RPC."
        ),
    ),
    (
        "influxdb-core-3.11.0-debian-13",
        (
            "InfluxDB 3 Core 3.11.0 cloud-init template for Debian 13 (Trixie), "
            "VMID 9052. Endpoint-agnostic: build dispatch supplies endpoint_id and "
            "target_node. First boot installs the pinned package, writes the managed "
            "loopback-only configuration, holds the package, and waits on the local "
            "readiness endpoint. Tokens, databases, and config changes are managed "
            "only through typed NMS RPC."
        ),
        (
            "InfluxDB 3 Core 3.11.0 cloud-init template for Debian 13 (Trixie), "
            "VMID 9052. Endpoint-agnostic: build dispatch selects an authorized enabled "
            "PackerBuildTarget URL and target_node. First boot installs the pinned package, "
            "writes the managed loopback-only configuration, holds the package, and waits "
            "on the local readiness endpoint. Tokens, databases, and config changes are "
            "managed only through typed NMS RPC."
        ),
    ),
    (
        "influxdb3-explorer-1.9.0-debian-13",
        (
            "InfluxDB 3 Explorer 1.9.0 cloud-init template for Debian 13 "
            "(Trixie), VMID 9053. Endpoint-agnostic: build dispatch supplies "
            "endpoint_id and target_node. The Explorer container is pinned by "
            "digest and binds to loopback by default; typed NMS provisioning "
            "supplies the vaulted Core connection after cloning."
        ),
        (
            "InfluxDB 3 Explorer 1.9.0 cloud-init template for Debian 13 "
            "(Trixie), VMID 9053. Endpoint-agnostic: build dispatch selects an "
            "authorized enabled PackerBuildTarget URL and target_node. The Explorer "
            "container is pinned by digest and binds to loopback by default; typed NMS "
            "provisioning supplies the vaulted Core connection after cloning."
        ),
    ),
)


def update_endpoint_authorization_descriptions(apps, schema_editor):
    """Replace only untouched historical seed descriptions."""

    del schema_editor
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")
    for name, old_description, new_description in DESCRIPTION_UPDATES:
        PackerTemplate.objects.filter(
            name=name,
            description=old_description,
        ).update(description=new_description)


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0031_seed_windows11"),
    ]

    operations = [
        migrations.RunPython(
            update_endpoint_authorization_descriptions,
            migrations.RunPython.noop,
        ),
    ]
