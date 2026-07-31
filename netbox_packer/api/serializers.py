import re

from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from ..models import PackerBuild, PackerBuildTarget, PackerInstallerConfig, PackerTemplate

_SECRET_KEY_PARTS = (
    "password",
    "passphrase",
    "secret",
    "token",
    "authorization",
    "api_key",
    "access_key",
    "private_key",
    "credential",
)
_SECRET_VALUE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        (
            r"(?:^|[,{]\s*|-\s+)[\"']?[A-Za-z0-9_.-]*"
            r"(?:token|password|passphrase|secret|authorization|api[-_]?key|"
            r"access[-_]?key|private[-_]?key|credential)"
            r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?(?!/)[^\s\"']+"
        ),
        r"\b(?:authorization|bearer)\s*[:=]\s*[\"']?[^\s\"']+",
        r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@",
        "\x00",
    )
)


def _contains_secret_material(value):
    """Reject secret-shaped keys and plaintext embedded in nested overrides."""

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized for part in _SECRET_KEY_PARTS):
                    return True
                pending.append(nested)
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and any(
            pattern.search(item) for pattern in _SECRET_VALUE_PATTERNS
        ):
            return True
    return False


class PackerTemplateBuildRequestSerializer(serializers.Serializer):
    """Typed build action payload; credentials never belong in overrides."""

    skip_node_validation = serializers.BooleanField(default=False)
    variable_overrides = serializers.JSONField(default=dict)

    def validate_variable_overrides(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("variable_overrides must be an object.")
        if _contains_secret_material(value):
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
                raise serializers.ValidationError(
                    "endpoint_id must be a positive integer."
                ) from exc
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
                raise serializers.ValidationError(
                    "template_vmid must be an integer >= 100."
                ) from exc
            if not 100 <= template_vmid <= 999999999:
                raise serializers.ValidationError("template_vmid must be an integer >= 100.")
            result["template_vmid"] = template_vmid
        target_node = str(result.get("target_node") or "").strip()
        if target_node:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", target_node):
                raise serializers.ValidationError("target_node has an invalid value.")
            result["target_node"] = target_node
        raw_storage = result.get("storage")
        if raw_storage not in (None, ""):
            storage = str(raw_storage).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", storage):
                raise serializers.ValidationError("storage has an invalid value.")
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
