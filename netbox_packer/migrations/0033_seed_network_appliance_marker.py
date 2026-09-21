"""Seed the generic network appliance service marker.

The migration creates or stamps a read-only ``provisions_service`` marker so
external build tooling that owns the generic network appliance profile can
discover the template row without maintaining cloud-init content in this plugin.

Only the marker is migration-managed. Cloud-init content, base image pin, node,
storage, VMID, and the installer config are supplied by the external build tooling
before dispatching a bake.

The placeholder row deliberately carries VMID 0. Proxmox and proxbox-api refuse
VMIDs below 100, so a clone request against the unbuilt placeholder fails closed
instead of cloning whatever guest happens to occupy the eventual VMID. Stamping
an existing row is compare-and-set: the row must already describe this profile
(Debian 13 and either the placeholder or the reserved VMID) and carry no marker;
any other row aborts the migration untouched, matching the collision behaviour of
the earlier seed migrations.
"""

from django.db import migrations

TEMPLATE_NAME = "network-appliance-debian-13"
SERVICE_MARKER = "network-appliance"
TEMPLATE_VMID = 9700
PLACEHOLDER_VMID = 0
PROXMOX_NODE = "select-at-build"
IDENTITY_FIELDS = {"os_family": "debian", "os_version": "13"}
ACCEPTED_VMIDS = (PLACEHOLDER_VMID, TEMPLATE_VMID)

DESCRIPTION = (
    "Generic network appliance template marker for Debian 13. Only the "
    "provisions_service marker is migration-managed. Cloud-init content, base "
    "image pin, Proxmox node, storage, template VMID, and installer config are "
    "owned and upserted by the external build tooling that bakes this profile."
)


def seed_network_appliance_marker(apps, schema_editor) -> None:
    del schema_editor
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    try:
        template = PackerTemplate.objects.get(name=TEMPLATE_NAME)
    except PackerTemplate.DoesNotExist:
        PackerTemplate.objects.create(
            name=TEMPLATE_NAME,
            os_family="debian",
            os_version="13",
            proxmox_template_id=PLACEHOLDER_VMID,
            proxmox_endpoint="",
            proxmox_node=PROXMOX_NODE,
            storage_pool="local",
            cloud_init_ready=True,
            build_status="pending",
            packer_template_ref="",
            install_qemu_guest_agent=False,
            install_zabbix_agent2=False,
            install_nms_agent=False,
            provisions_service=SERVICE_MARKER,
            installer_config=None,
            description=DESCRIPTION,
        )
        return

    existing_marker = template.provisions_service or ""
    mismatches = _identity_mismatches(template)
    if existing_marker and existing_marker != SERVICE_MARKER:
        mismatches.append("provisions_service")
    if mismatches:
        raise RuntimeError(
            f"Network appliance seed naming collision: template "
            f"{TEMPLATE_NAME!r} already exists with different values for "
            f"{', '.join(mismatches)}. Rename the existing row, or delete it if it "
            "is genuinely obsolete, then rerun the migration. No existing row was "
            "modified."
        )

    if existing_marker == SERVICE_MARKER:
        return
    # Conditional update: the identity re-checked in the WHERE clause closes the
    # window between the read above and the write, so a concurrent edit cannot
    # attach the marker to a row the migration would have rejected.
    updated = PackerTemplate.objects.filter(
        pk=template.pk,
        provisions_service="",
        proxmox_template_id__in=ACCEPTED_VMIDS,
        **IDENTITY_FIELDS,
    ).update(provisions_service=SERVICE_MARKER)
    if updated != 1:
        raise RuntimeError(
            f"Network appliance seed naming collision: template {TEMPLATE_NAME!r} "
            "changed while the migration ran. Rerun the migration. No existing row "
            "was modified."
        )


def _identity_mismatches(template) -> list[str]:
    """Return the identity fields on which an existing row differs from this profile."""
    mismatches = [field for field, expected in IDENTITY_FIELDS.items() if getattr(template, field) != expected]
    if template.proxmox_template_id not in ACCEPTED_VMIDS:
        mismatches.append("proxmox_template_id")
    return mismatches


def unseed_network_appliance_marker(apps, schema_editor) -> None:
    # Golden-template seed rollbacks are intentionally non-destructive: an operator
    # may already have baked the reserved VMID or edited the database rows.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0032_update_endpoint_authorization_descriptions"),
    ]

    operations = [
        migrations.RunPython(
            seed_network_appliance_marker,
            unseed_network_appliance_marker,
        ),
    ]
