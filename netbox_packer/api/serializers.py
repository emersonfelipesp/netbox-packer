from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from ..models import PackerBuild, PackerBuildTarget, PackerInstallerConfig, PackerTemplate


class PackerInstallerConfigSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_packer-api:packerinstallerconfig-detail",
    )

    class Meta:
        model = PackerInstallerConfig
        fields = (
            "id",
            "url",
            "display",
            "name",
            "os_family",
            "installer_type",
            "content",
            "version",
            "checksum",
            "description",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name", "version")


class PackerTemplateSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_packer-api:packertemplate-detail",
    )
    installer_config = PackerInstallerConfigSerializer(nested=True, required=False, allow_null=True)

    class Meta:
        model = PackerTemplate
        fields = (
            "id",
            "url",
            "display",
            "name",
            "os_family",
            "os_version",
            "proxmox_template_id",
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
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name", "os_family", "os_version", "build_status")


class PackerBuildSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_packer-api:packerbuild-detail",
    )
    template = PackerTemplateSerializer(nested=True)

    class Meta:
        model = PackerBuild
        fields = (
            "id",
            "url",
            "display",
            "template",
            "triggered_by",
            "queued_at",
            "started_at",
            "finished_at",
            "status",
            "variable_overrides",
            "log",
            "exit_code",
            "result_template_id",
            "selected_node",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "status", "queued_at")


class PackerBuildTargetSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_packer-api:packerbuildtarget-detail",
    )
    template = PackerTemplateSerializer(nested=True)

    class Meta:
        model = PackerBuildTarget
        fields = (
            "id",
            "url",
            "display",
            "template",
            "proxmox_node",
            "priority",
            "enabled",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "proxmox_node", "enabled")
