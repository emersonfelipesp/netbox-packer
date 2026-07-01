import json

from django import forms
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms import add_blank_choice
from utilities.forms.fields import DynamicModelChoiceField, TagFilterField
from utilities.forms.rendering import FieldSet

from .choices import (
    OS_VERSIONS_BY_FAMILY,
    BuildStatusChoices,
    OSFamilyChoices,
    os_version_grouped_choices,
    os_version_known_values,
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


# ── PackerTemplate ────────────────────────────────────────────────────────────


class PackerTemplateForm(NetBoxModelForm):
    os_version = forms.ChoiceField(
        choices=(),
        help_text=(
            "Pick a release for the selected OS family. The list narrows to the "
            "chosen family; every version stays selectable if JavaScript is off."
        ),
    )
    installer_config = DynamicModelChoiceField(
        queryset=PackerInstallerConfig.objects.all(),
        required=False,
        help_text="For a cloud-init template, select an installer config whose type is 'Cloud-config YAML'.",
    )

    class Media:
        js = ("netbox_packer/os_version_filter.js",)

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
            "max_age_days",
            "auto_rebuild",
            "description",
            "hcp_bucket_name",
            "hcp_channel_name",
            "hcp_iteration_id",
            "hcp_build_id",
            "hcp_last_synced_at",
            "installer_config",
            "install_qemu_guest_agent",
            "install_zabbix_agent2",
            "zabbix_server",
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
                "max_age_days",
                "auto_rebuild",
                name="Build",
            ),
            FieldSet(
                "installer_config",
                name="Installer",
            ),
            FieldSet(
                "install_qemu_guest_agent",
                "install_zabbix_agent2",
                "zabbix_server",
                name="Monitoring Agents",
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
        help_texts = {
            "proxmox_template_id": "Proxmox VMID the baked template will occupy (must be free on the target node).",
            "storage_pool": "Proxmox storage pool that will hold the template disk (e.g. 'local', 'local-lvm').",
            "cloud_init_ready": "Leave enabled for cloud-init images so clones receive user-data at first boot.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Grouped (optgroup-by-family) choices; works fully without JavaScript.
        grouped = os_version_grouped_choices()

        # Never drop an existing template's stored version: if the persisted
        # value is outside the offered lists, keep it selectable so editing an
        # older template does not fail validation.
        current = self.instance.os_version if self.instance and self.instance.pk else None
        if current and current not in os_version_known_values():
            grouped = [*grouped, (f"{current} (current)", [(current, current)])]

        self.fields["os_version"].choices = add_blank_choice(grouped)

        # Expose the family→versions map so the progressive-enhancement script
        # can narrow the dropdown to the selected OS family.
        self.fields["os_version"].widget.attrs["data-os-version-map"] = json.dumps(OS_VERSIONS_BY_FAMILY)

        # Opt os_version out of NetBox's Tom Select enhancement. Tom Select owns
        # the rendered dropdown from its own option registry, so the native
        # DOM narrowing in os_version_filter.js would silently no-op against it.
        # A plain <select> lets the script drive the visible list, and for a
        # short release list a non-searchable select is also the better UX.
        existing_class = self.fields["os_version"].widget.attrs.get("class", "")
        self.fields["os_version"].widget.attrs["class"] = f"{existing_class} no-ts".strip()

    def clean(self):
        """Reject an os_version that does not belong to the selected os_family.

        This is the server-side counterpart to the ``os_version_filter.js``
        progressive enhancement, closing the case where JavaScript is disabled.
        It is intentionally scoped to this UI form only — the model field and
        REST serializer keep ``os_version`` free-form so automation may send any
        version. An existing template's originally-stored value is always allowed
        so editing an older (off-list) template never fails validation.
        """
        cleaned_data = super().clean()

        family = cleaned_data.get("os_family")
        version = cleaned_data.get("os_version")

        if not family or not version:
            return cleaned_data

        # Preserve the persisted value on edit (mirrors the __init__ guard).
        stored = self.instance.os_version if self.instance and self.instance.pk else None
        if version == stored:
            return cleaned_data

        allowed = {value for value, _label in OS_VERSIONS_BY_FAMILY.get(family, [])}
        if version not in allowed:
            family_label = dict(OSFamilyChoices).get(family, family)
            self.add_error(
                "os_version",
                f"'{version}' is not a valid version for OS family '{family_label}'. "
                "Choose a version that belongs to the selected OS family.",
            )

        return cleaned_data


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
