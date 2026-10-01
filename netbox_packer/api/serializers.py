import re

from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from ..base_image import validate_base_image_url
from ..endpoint_authorization import PackerEndpointAuthorizationError, _split_absolute_http_url
from ..models import (
    PackerBuild,
    PackerBuildTarget,
    PackerInstallerConfig,
    PackerTemplate,
)

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
                if normalized in {"image_url", "base_image_url"} and nested not in (None, ""):
                    try:
                        validate_base_image_url(nested, source=str(key))
                    except ValueError:
                        return True
                pending.append(nested)
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and any(pattern.search(item) for pattern in _SECRET_VALUE_PATTERNS):
            return True
    return False


def _proxmox_endpoint_text_problem(raw):
    """Return why raw endpoint text is unsafe to persist, or None."""

    if any(char.isspace() or ord(char) < 32 for char in raw):
        return "Proxmox endpoint URL must not contain whitespace or control characters."
    if "?" in raw or "#" in raw:
        return "Proxmox endpoint URL must not contain a query string or fragment."
    if _contains_secret_material(raw):
        return "Proxmox endpoint URL must not contain secret-shaped values."
    return None


def _proxmox_endpoint_structure_problem(raw):
    """Return why a text-safe endpoint would be refused at dispatch, or None.

    Delegates to the exact parser dispatch authorization uses, so the API can
    never store a URL (for example one carrying a path) that authorization
    would later reject for every build.
    """

    try:
        _split_absolute_http_url(raw, label="Proxmox endpoint URL")
    except PackerEndpointAuthorizationError as exc:
        return str(exc)
    return None


def _validate_proxmox_endpoint_url(value):
    """Validate a stored Proxmox endpoint URL without echoing the submitted value.

    The URL only identifies which netbox-proxbox endpoint a build targets; it is
    never itself authorization. Credentials, query strings, and fragments are
    refused so no secret can be persisted in this field.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    problem = _proxmox_endpoint_text_problem(raw) or _proxmox_endpoint_structure_problem(raw)
    if problem:
        raise serializers.ValidationError(problem)
    return raw


class _ProxmoxEndpointValidationMixin:
    """Shared API boundary validation for models that persist ``proxmox_endpoint``."""

    def validate_proxmox_endpoint(self, value):
        """Reject credentialed or malformed endpoint URLs before they are stored."""

        return _validate_proxmox_endpoint_url(value)


class PackerTemplateBuildRequestSerializer(serializers.Serializer):
    """Typed build action payload; credentials never belong in overrides."""

    skip_node_validation = serializers.BooleanField(default=False)
    variable_overrides = serializers.JSONField(default=dict)

    def validate_variable_overrides(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("variable_overrides must be an object.")
        if _contains_secret_material(value):
            raise serializers.ValidationError("Secret-shaped override keys or values are forbidden.")
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


class PackerTemplateSerializer(_ProxmoxEndpointValidationMixin, NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_packer-api:packertemplate-detail",
    )
    installer_config = PackerInstallerConfigSerializer(nested=True, required=False, allow_null=True)

    def validate_base_image_url(self, value):
        """Keep inline credentials out of persisted template image pins."""

        if not value:
            return value
        try:
            return validate_base_image_url(value, source="base_image_url")
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

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
            "install_qemu_guest_agent",
            "install_zabbix_agent2",
            "zabbix_server",
            "base_image_url",
            "base_image_sha256",
            "base_image_url_at_build",
            "base_image_sha256_at_build",
            "provisions_service",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name", "os_family", "os_version", "build_status")
        read_only_fields = (
            "provisions_service",
            "base_image_url_at_build",
            "base_image_sha256_at_build",
        )


class PackerBuildSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_packer-api:packerbuild-detail",
    )
    template = PackerTemplateSerializer(nested=True)

    def validate_variable_overrides(self, value):
        """Apply the secret boundary to generic build create/edit requests too."""

        if not isinstance(value, dict):
            raise serializers.ValidationError("variable_overrides must be an object.")
        if _contains_secret_material(value):
            raise serializers.ValidationError(
                "Secret-shaped override keys or values are forbidden; use opaque secret references."
            )
        return value

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
            "base_image_url_at_build",
            "base_image_sha256_at_build",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "status", "queued_at")
        read_only_fields = ("base_image_url_at_build", "base_image_sha256_at_build")


class PackerBuildTargetSerializer(_ProxmoxEndpointValidationMixin, NetBoxModelSerializer):
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
            "proxmox_endpoint",
            "proxmox_node",
            "priority",
            "enabled",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "proxmox_node", "enabled")
