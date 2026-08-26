"""Add an immutable golden-template flag and stamp the trusted File Server row.

`PackerTemplate.name` uniqueness (migration 0018) only prevents two rows
sharing the trusted name *simultaneously*. It does not stop the trusted row
being renamed away and a different row later reclaiming the freed name — that
row would then pass the File Server package-index credential guard in
package_index.py, which historically authorized by name alone.

`is_fileserver_golden_template` is `editable=False` (excluded from
PackerTemplateForm and the DRF serializer's explicit `fields` tuples, so it
can only ever be set here, by a migration) and is stamped exactly once, on
the row matching the name the File Server template has carried since
migrations 0014/0017. Renaming that row afterwards leaves the flag set (the
template is still the same trusted template); any other row, including one
that later reclaims the vacated name, defaults to False and is rejected.
"""

from django.db import migrations, models

# Must match the `TEMPLATE_NAME` seeded by migrations 0014/0017 exactly. Kept
# as its own copy per Django migration immutability convention.
TEMPLATE_NAME = "tpl-fileserver-allinone-ubuntu-2404"


def stamp_golden_template(apps, schema_editor):
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")
    PackerTemplate.objects.filter(name=TEMPLATE_NAME).update(is_fileserver_golden_template=True)


def unstamp_golden_template(apps, schema_editor):
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")
    PackerTemplate.objects.filter(name=TEMPLATE_NAME).update(is_fileserver_golden_template=False)


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0018_alter_packertemplate_name_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="packertemplate",
            name="is_fileserver_golden_template",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.RunPython(stamp_golden_template, unstamp_golden_template),
    ]
