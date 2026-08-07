from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from ..models import PackerBuild, PackerBuildTarget, PackerInstallerConfig, PackerTemplate
from ..security import contains_non_persistable_build_override

_PACKER_BUILD_PUBLIC_IMMUTABLE_FIELDS = frozenset(
    {
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
        "proxbox_operation_id",
    }
)


def _contains_secret_material(value):
    """Compatibility name for the shared durable-override detector."""
    return contains_non_persistable_build_override(value)


class PackerTemplateBuildRequestSerializer(serializers.Serializer):
    """Typed build action payload; credentials never belong in overrides."""

    skip_node_validation = serializers.BooleanField(default=False)
    variable_overrides = serializers.JSONField(default=dict)

    def validate_variable_overrides(self, value):
        from ..models import validate_packer_build_overrides

        if not isinstance(value, dict):
            raise serializers.ValidationError("variable_overrides must be an object.")
        validate_packer_build_overrides(value)
        if contains_non_persistable_build_override(value):
            raise serializers.ValidationError(
                "Secret-shaped override keys or values are forbidden; use netbox-nms references."
            )
        result = dict(value)
        endpoint_id = result.get("endpoint_id")
        if endpoint_id not in (None, ""):
            if isinstance(endpoint_id, bool):
                raise serializers.ValidationError("endpoint_id must be a positive integer.")
            try:
                endpoint_id = int(endpoint_id)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError("endpoint_id must be a positive integer.") from exc
            if endpoint_id < 1:
                raise serializers.ValidationError("endpoint_id must be a positive integer.")
            result["endpoint_id"] = endpoint_id
        template_vmid = result.get("template_vmid")
        if template_vmid not in (None, ""):
            if isinstance(template_vmid, bool):
                raise serializers.ValidationError("template_vmid must be an integer >= 100.")
            try:
                template_vmid = int(template_vmid)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError("template_vmid must be an integer >= 100.") from exc
            if not 100 <= template_vmid <= 999999999:
                raise serializers.ValidationError("template_vmid must be an integer >= 100.")
            result["template_vmid"] = template_vmid
        target_node = str(result.get("target_node") or "").strip()
        if target_node:
            from ..proxbox_client import validate_proxbox_target_node

            try:
                target_node = validate_proxbox_target_node(target_node)
            except ValueError as exc:
                raise serializers.ValidationError("target_node has an invalid value.") from exc

            result["target_node"] = target_node
        raw_storage = result.get("storage")
        if raw_storage not in (None, ""):
            from ..proxbox_client import validate_proxbox_storage_identifier

            try:
                storage = validate_proxbox_storage_identifier(str(raw_storage).strip())
            except ValueError as exc:
                raise serializers.ValidationError("storage has an invalid value.") from exc
            result["storage"] = storage
        return result


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
            "install_qemu_guest_agent",
            "install_zabbix_agent2",
            "zabbix_server",
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
    template = PackerTemplateSerializer(nested=True, read_only=True)

    def validate_variable_overrides(self, value):
        from ..models import validate_packer_build_overrides

        validate_packer_build_overrides(value)
        return value

    def validate(self, attrs):
        submitted = _PACKER_BUILD_PUBLIC_IMMUTABLE_FIELDS.intersection(getattr(self, "initial_data", {}))
        if submitted:
            raise serializers.ValidationError(
                dict.fromkeys(sorted(submitted), "This worker-managed field cannot be submitted.")
            )
        return super().validate(attrs)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if "variable_overrides" in representation:
            representation["variable_overrides"] = instance.safe_variable_overrides
        if "log" in representation:
            representation["log"] = instance.safe_log
        return representation

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
            "proxbox_operation_id",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "status", "queued_at")
        read_only_fields = tuple(sorted(_PACKER_BUILD_PUBLIC_IMMUTABLE_FIELDS))


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
