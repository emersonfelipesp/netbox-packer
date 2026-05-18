import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("extras", "0001_initial"),
    ]

    operations = [
        # ── PackerInstallerConfig ─────────────────────────────────────────────
        migrations.CreateModel(
            name="PackerInstallerConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("os_family", models.CharField(max_length=20)),
                ("installer_type", models.CharField(max_length=20)),
                ("content", models.TextField()),
                ("version", models.CharField(default="1.0.0", max_length=40)),
                ("checksum", models.CharField(blank=True, editable=False, max_length=64)),
                ("description", models.TextField(blank=True)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "verbose_name": "Packer Installer Config",
                "verbose_name_plural": "Packer Installer Configs",
                "ordering": ["name", "version"],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddConstraint(
            model_name="packerinstallerconfig",
            constraint=models.UniqueConstraint(
                fields=["name", "version"],
                name="netbox_packer_packerinstallerconfig_name_version_uniq",
            ),
        ),
        # ── PackerTemplate ────────────────────────────────────────────────────
        migrations.CreateModel(
            name="PackerTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("os_family", models.CharField(max_length=20)),
                ("os_version", models.CharField(max_length=40)),
                ("proxmox_template_id", models.PositiveIntegerField()),
                ("proxmox_endpoint", models.URLField(blank=True)),
                ("proxmox_node", models.CharField(max_length=100)),
                ("storage_pool", models.CharField(blank=True, max_length=100)),
                ("storage_pool_type", models.CharField(blank=True, max_length=20)),
                ("storage_format", models.CharField(blank=True, max_length=10)),
                ("cloud_init_ready", models.BooleanField(default=True)),
                ("min_cpu_type", models.CharField(blank=True, max_length=40)),
                (
                    "build_status",
                    models.CharField(default="pending", max_length=20),
                ),
                ("built_at", models.DateTimeField(blank=True, null=True)),
                ("packer_template_ref", models.CharField(blank=True, max_length=255)),
                (
                    "max_age_days",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        help_text="Rebuild template after this many days",
                    ),
                ),
                ("auto_rebuild", models.BooleanField(default=False)),
                ("description", models.TextField(blank=True)),
                # HCP Packer fields
                ("hcp_bucket_name", models.CharField(blank=True, max_length=255)),
                ("hcp_channel_name", models.CharField(blank=True, max_length=255)),
                ("hcp_iteration_id", models.CharField(blank=True, max_length=255)),
                ("hcp_build_id", models.CharField(blank=True, max_length=255)),
                ("hcp_last_synced_at", models.DateTimeField(blank=True, null=True)),
                # Installer config
                (
                    "installer_config",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="templates",
                        to="netbox_packer.packerinstallerconfig",
                    ),
                ),
                (
                    "installer_config_checksum_at_build",
                    models.CharField(blank=True, max_length=64),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "verbose_name": "Packer Template",
                "verbose_name_plural": "Packer Templates",
                "ordering": ["name"],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        # ── PackerBuild ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name="PackerBuild",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="builds",
                        to="netbox_packer.packertemplate",
                    ),
                ),
                ("triggered_by", models.CharField(blank=True, max_length=100)),
                ("queued_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(default="queued", max_length=20)),
                (
                    "variable_overrides",
                    models.JSONField(blank=True, default=dict),
                ),
                ("log", models.TextField(blank=True)),
                ("exit_code", models.IntegerField(blank=True, null=True)),
                ("result_template_id", models.IntegerField(blank=True, null=True)),
                ("selected_node", models.CharField(blank=True, max_length=100)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "verbose_name": "Packer Build",
                "verbose_name_plural": "Packer Builds",
                "ordering": ["-queued_at"],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        # ── PackerBuildTarget ─────────────────────────────────────────────────
        migrations.CreateModel(
            name="PackerBuildTarget",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="build_targets",
                        to="netbox_packer.packertemplate",
                    ),
                ),
                ("proxmox_endpoint", models.URLField(blank=True)),
                ("proxmox_node", models.CharField(max_length=100)),
                ("priority", models.PositiveIntegerField(default=10)),
                ("enabled", models.BooleanField(default=True)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "verbose_name": "Packer Build Target",
                "verbose_name_plural": "Packer Build Targets",
                "ordering": ["priority", "proxmox_node"],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddConstraint(
            model_name="packerbuildtarget",
            constraint=models.UniqueConstraint(
                fields=["template", "proxmox_node"],
                name="netbox_packer_packerbuildtarget_template_node_uniq",
            ),
        ),
    ]
