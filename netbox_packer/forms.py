from django import forms
from netbox.forms import NetBoxModelBulkEditForm, NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms.fields import DynamicModelChoiceField, TagFilterField
from utilities.forms.rendering import FieldSet

from .choices import (
    BuildStatusChoices,
    OSFamilyChoices,
)
from .models import PackerBuild, PackerBuildTarget, PackerInstallerConfig, PackerTemplate

# ── PackerInstallerConfig ─────────────────────────────────────────────────────


class PackerInstallerConfigForm(NetBoxModelForm):
    class Meta:
        model = PackerInstallerConfig
        fields = (
            "name",
            "os_family",
            "installer_type",
            "content",
            "version",
            "description",
            "tags",
        )
        fieldsets = (
            FieldSet("name", "os_family", "installer_type", "version", name="Identity"),
            FieldSet("content", name="Content"),
            FieldSet("description", "tags", name="Metadata"),
        )


class PackerInstallerConfigFilterForm(NetBoxModelFilterSetForm):
    model = PackerInstallerConfig
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("name", "os_family", "installer_type", name="Attributes"),
    )
    os_family = forms.MultipleChoiceField(
        choices=OSFamilyChoices,
        required=False,
    )
    tag = TagFilterField(model)


class PackerInstallerConfigBulkEditForm(NetBoxModelBulkEditForm):
    model = PackerInstallerConfig
    os_family = forms.ChoiceField(choices=OSFamilyChoices, required=False)
    fieldsets = (FieldSet("os_family", name="Installer Config"),)
    nullable_fields = ()


# ── PackerTemplate ────────────────────────────────────────────────────────────


class PackerTemplateForm(NetBoxModelForm):
    installer_config = DynamicModelChoiceField(
        queryset=PackerInstallerConfig.objects.all(),
        required=False,
    )

    class Meta:
        model = PackerTemplate
        fields = (
            "name",
            "os_family",
            "os_version",
            "proxmox_template_id",
            "proxmox_endpoint",
            "proxmox_node",
            "storage_pool",
            "storage_pool_type",
            "storage_format",
            "cloud_init_ready",
            "min_cpu_type",
            "build_status",
            "built_at",
            "packer_template_ref",
            "max_age_days",
            "auto_rebuild",
            "description",
            "hcp_bucket_name",
            "hcp_channel_name",
            "hcp_iteration_id",
            "hcp_build_id",
            "hcp_last_synced_at",
            "installer_config",
            "installer_config_checksum_at_build",
            "tags",
        )
        fieldsets = (
            FieldSet("name", "os_family", "os_version", name="Identity"),
            FieldSet(
                "proxmox_template_id",
                "proxmox_endpoint",
                "proxmox_node",
                "storage_pool",
                "storage_pool_type",
                "storage_format",
                "cloud_init_ready",
                "min_cpu_type",
                name="Proxmox",
            ),
            FieldSet(
                "build_status",
                "built_at",
                "packer_template_ref",
                "max_age_days",
                "auto_rebuild",
                name="Build",
            ),
            FieldSet(
                "installer_config",
                "installer_config_checksum_at_build",
                name="Installer",
            ),
            FieldSet(
                "hcp_bucket_name",
                "hcp_channel_name",
                "hcp_iteration_id",
                "hcp_build_id",
                "hcp_last_synced_at",
                name="HCP Packer",
            ),
            FieldSet("description", "tags", name="Metadata"),
        )


class PackerTemplateFilterForm(NetBoxModelFilterSetForm):
    model = PackerTemplate
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("name", "os_family", "os_version", name="Identity"),
        FieldSet("build_status", "cloud_init_ready", "proxmox_node", name="Build"),
    )
    os_family = forms.MultipleChoiceField(
        choices=OSFamilyChoices,
        required=False,
    )
    build_status = forms.MultipleChoiceField(
        choices=BuildStatusChoices,
        required=False,
    )
    cloud_init_ready = forms.NullBooleanSelect()
    tag = TagFilterField(model)


class PackerTemplateBulkEditForm(NetBoxModelBulkEditForm):
    model = PackerTemplate
    os_family = forms.ChoiceField(choices=OSFamilyChoices, required=False)
    build_status = forms.ChoiceField(choices=BuildStatusChoices, required=False)
    cloud_init_ready = forms.NullBooleanField(required=False)
    auto_rebuild = forms.NullBooleanField(required=False)
    max_age_days = forms.IntegerField(required=False)
    fieldsets = (
        FieldSet("os_family", "build_status", "cloud_init_ready", "auto_rebuild", "max_age_days", name="Template"),
    )
    nullable_fields = ("max_age_days",)


# ── PackerBuild ───────────────────────────────────────────────────────────────


class PackerBuildForm(NetBoxModelForm):
    template = DynamicModelChoiceField(queryset=PackerTemplate.objects.all())

    class Meta:
        model = PackerBuild
        fields = (
            "template",
            "triggered_by",
            "variable_overrides",
            "tags",
        )
        fieldsets = (
            FieldSet("template", "triggered_by", name="Build"),
            FieldSet("variable_overrides", name="Overrides"),
            FieldSet("tags", name="Metadata"),
        )


class PackerBuildFilterForm(NetBoxModelFilterSetForm):
    model = PackerBuild
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("template_id", "status", "selected_node", name="Filters"),
    )
    template_id = DynamicModelChoiceField(
        queryset=PackerTemplate.objects.all(),
        required=False,
        label="Template",
    )
    tag = TagFilterField(model)


class PackerBuildBulkEditForm(NetBoxModelBulkEditForm):
    model = PackerBuild
    status = forms.ChoiceField(
        choices=[("queued", "Queued"), ("running", "Running"), ("success", "Success"), ("failed", "Failed"), ("cancelled", "Cancelled")],
        required=False,
    )
    fieldsets = (FieldSet("status", name="Build"),)
    nullable_fields = ()


# ── PackerBuildTarget ─────────────────────────────────────────────────────────


class PackerBuildTargetForm(NetBoxModelForm):
    template = DynamicModelChoiceField(queryset=PackerTemplate.objects.all())

    class Meta:
        model = PackerBuildTarget
        fields = (
            "template",
            "proxmox_endpoint",
            "proxmox_node",
            "priority",
            "enabled",
            "tags",
        )
        fieldsets = (
            FieldSet("template", "proxmox_endpoint", "proxmox_node", "priority", "enabled", name="Target"),
            FieldSet("tags", name="Metadata"),
        )


class PackerBuildTargetBulkEditForm(NetBoxModelBulkEditForm):
    model = PackerBuildTarget
    priority = forms.IntegerField(required=False)
    enabled = forms.NullBooleanField(required=False)
    fieldsets = (FieldSet("priority", "enabled", name="Build Target"),)
    nullable_fields = ()


class PackerBuildTargetFilterForm(NetBoxModelFilterSetForm):
    model = PackerBuildTarget
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("template_id", "proxmox_node", "enabled", name="Filters"),
    )
    template_id = DynamicModelChoiceField(
        queryset=PackerTemplate.objects.all(),
        required=False,
        label="Template",
    )
    tag = TagFilterField(model)
