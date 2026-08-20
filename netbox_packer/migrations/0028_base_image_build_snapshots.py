"""Record the resolved base image used by successful cloud-image builds.

The desired pin is writable, but these snapshots are machine-managed provenance.
They let staleness detection invalidate an artifact when its declared pin changes or
when a per-build override baked bytes that do not match the template declaration.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0027_packertemplate_base_image_pin"),
    ]

    operations = [
        migrations.AddField(
            model_name="packerbuild",
            name="base_image_url_at_build",
            field=models.URLField(
                blank=True,
                default="",
                editable=False,
                help_text="Resolved base image URL used by this successful cloud-image build.",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="packerbuild",
            name="base_image_sha256_at_build",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Resolved base image sha256 used by this successful cloud-image build.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="base_image_url_at_build",
            field=models.URLField(
                blank=True,
                default="",
                editable=False,
                help_text="Resolved base image URL used by the last successful cloud-image build.",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="base_image_sha256_at_build",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Resolved base image sha256 used by the last successful cloud-image build.",
                max_length=64,
            ),
        ),
    ]
