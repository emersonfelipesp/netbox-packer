"""Migration: creates PackerPluginSettings table."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0002_squashed_0059"),
        ("netbox_packer", "0002_vm_lineage_custom_field"),
    ]

    operations = [
        migrations.CreateModel(
            name="PackerPluginSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(blank=True, default=dict, encoder=None),
                ),
                (
                    "singleton_key",
                    models.CharField(
                        default="default",
                        editable=False,
                        max_length=32,
                        unique=True,
                    ),
                ),
                (
                    "branching_enabled",
                    models.BooleanField(default=False),
                ),
                (
                    "branch_name_prefix",
                    models.CharField(default="packer-stale", max_length=64),
                ),
                (
                    "branch_on_conflict",
                    models.CharField(
                        choices=[
                            ("fail", "Fail and leave branch open for review"),
                            ("acknowledge", "Acknowledge conflicts and merge anyway"),
                        ],
                        default="fail",
                        max_length=16,
                    ),
                ),
            ],
            options={
                "verbose_name": "Packer Plugin Settings",
                "verbose_name_plural": "Packer Plugin Settings",
            },
        ),
    ]
