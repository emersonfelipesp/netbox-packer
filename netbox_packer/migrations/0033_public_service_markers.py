from django.db import migrations, models

PUBLIC_SERVICE_MARKERS = (
    ("akvorado-2.4.0-ubuntu-2404", "akvorado"),
    ("influxdb-core-3.11.0-debian-13", "influxdb3-core"),
    ("influxdb3-explorer-1.9.0-debian-13", "influxdb3-explorer"),
)


def _table_columns(connection, table_name):
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def ensure_service_marker_column(apps, schema_editor):
    """Add the public marker only when an earlier installation did not."""

    template_model = apps.get_model("netbox_packer", "PackerTemplate")
    table_name = template_model._meta.db_table
    if "provisions_service" in _table_columns(schema_editor.connection, table_name):
        return

    field = models.CharField(blank=True, default="", editable=False, max_length=64)
    field.set_attributes_from_name("provisions_service")
    schema_editor.add_field(template_model, field)


def stamp_public_service_markers(apps, schema_editor):
    """Apply public service markers without modifying unrelated rows."""

    template_model = apps.get_model("netbox_packer", "PackerTemplate")
    for template_name, service_marker in PUBLIC_SERVICE_MARKERS:
        template_model.objects.filter(name=template_name, provisions_service="").update(
            provisions_service=service_marker
        )


class Migration(migrations.Migration):
    dependencies = [("netbox_packer", "0032_update_endpoint_authorization_descriptions")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_service_marker_column, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="packertemplate",
                    name="provisions_service",
                    field=models.CharField(blank=True, default="", editable=False, max_length=64),
                ),
            ],
        ),
        migrations.RunPython(stamp_public_service_markers, migrations.RunPython.noop),
    ]
