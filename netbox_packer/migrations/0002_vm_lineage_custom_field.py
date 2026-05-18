"""Add source_packer_template custom field to VirtualMachine."""
from django.db import migrations

CUSTOM_FIELD_NAME = "source_packer_template"


def create_vm_lineage_custom_field(apps, schema_editor):
    """Create source_packer_template integer custom field on VirtualMachine."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    CustomField = apps.get_model("extras", "CustomField")

    try:
        vm_ct = ContentType.objects.get(app_label="virtualization", model="virtualmachine")
    except ContentType.DoesNotExist:
        # Virtualization app not installed — skip
        return

    cf, created = CustomField.objects.get_or_create(
        name=CUSTOM_FIELD_NAME,
        defaults={
            "label": "Source Packer Template",
            "description": (
                "ID of the netbox_packer.PackerTemplate this VM was cloned from. "
                "Set by proxbox-api on VM creation when the template VMID matches."
            ),
            "type": "integer",
            "required": False,
            "ui_visible": "always",
            "ui_editable": "yes",
        },
    )
    if not created:
        return
    cf.object_types.add(vm_ct)


def remove_vm_lineage_custom_field(apps, schema_editor):
    """Remove source_packer_template custom field (reverse migration)."""
    CustomField = apps.get_model("extras", "CustomField")
    CustomField.objects.filter(name=CUSTOM_FIELD_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0001_initial"),
        ("extras", "0001_initial"),
        ("virtualization", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_vm_lineage_custom_field,
            remove_vm_lineage_custom_field,
        ),
    ]
