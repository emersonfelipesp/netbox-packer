import django_filters
from netbox.filtersets import NetBoxModelFilterSet

from .choices import BuildStatusChoices, OSFamilyChoices
from .models import PackerBuild, PackerBuildTarget, PackerInstallerConfig, PackerTemplate


class PackerTemplateFilterSet(NetBoxModelFilterSet):
    os_family = django_filters.MultipleChoiceFilter(choices=OSFamilyChoices)
    build_status = django_filters.MultipleChoiceFilter(choices=BuildStatusChoices)
    cloud_init_ready = django_filters.BooleanFilter()

    class Meta:
        model = PackerTemplate
        fields = (
            "id",
            "name",
            "os_family",
            "os_version",
            "build_status",
            "cloud_init_ready",
            "proxmox_node",
            "auto_rebuild",
        )

    def search(self, queryset, name, value):
        return queryset.filter(name__icontains=value) | queryset.filter(os_version__icontains=value)


class PackerBuildFilterSet(NetBoxModelFilterSet):
    template_id = django_filters.ModelMultipleChoiceFilter(
        queryset=PackerTemplate.objects.all(),
        label="Template (ID)",
    )

    class Meta:
        model = PackerBuild
        fields = (
            "id",
            "template_id",
            "status",
            "triggered_by",
            "selected_node",
        )

    def search(self, queryset, name, value):
        return queryset.filter(triggered_by__icontains=value)


class PackerInstallerConfigFilterSet(NetBoxModelFilterSet):
    os_family = django_filters.MultipleChoiceFilter(choices=OSFamilyChoices)

    class Meta:
        model = PackerInstallerConfig
        fields = (
            "id",
            "name",
            "os_family",
            "installer_type",
            "version",
        )

    def search(self, queryset, name, value):
        return queryset.filter(name__icontains=value)


class PackerBuildTargetFilterSet(NetBoxModelFilterSet):
    template_id = django_filters.ModelMultipleChoiceFilter(
        queryset=PackerTemplate.objects.all(),
        label="Template (ID)",
    )
    enabled = django_filters.BooleanFilter()

    class Meta:
        model = PackerBuildTarget
        fields = (
            "id",
            "template_id",
            "proxmox_node",
            "priority",
            "enabled",
        )

    def search(self, queryset, name, value):
        return queryset.filter(proxmox_node__icontains=value)
