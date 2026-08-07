import re

from django.db import migrations, models

import netbox_packer.models

_LEGACY_PROXBOX_LOG_PREFIX = "[INFO] Cloud-init template image build for "
_LEGACY_UNSAFE_LOG_MARKERS = ("[INFO] Base image: ", "[BUILD_SCRIPT]", "[STDOUT]", "[STDERR]")
_REDACTED_PACKER_LOG = (
    "[REDACTED] Build log removed because it could contain credentials, signed URLs, "
    "generated scripts, or process output."
)
_NON_PERSISTABLE_OVERRIDE_KEYS = frozenset({"image_url"})
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
_REDACTED_OVERRIDE_VALUE = "[REDACTED]"


def _is_secret_override_key(value):
    normalized = str(value).lower().replace("-", "_")
    return normalized in _NON_PERSISTABLE_OVERRIDE_KEYS or any(part in normalized for part in _SECRET_KEY_PARTS)


def _is_secret_override_value(value):
    return isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def _contains_non_persistable_override(value):
    """Detect secret-shaped keys and values without importing mutable app code."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, nested in item.items():
                if _is_secret_override_key(key):
                    return True
                pending.append(nested)
        elif isinstance(item, list):
            pending.extend(item)
        elif _is_secret_override_value(item):
            return True
    return False


def _redact_non_persistable_overrides(value):
    """Copy nested historical JSON while removing secret-shaped material."""
    if isinstance(value, dict):
        return {
            key: _redact_non_persistable_overrides(item)
            for key, item in value.items()
            if not _is_secret_override_key(key)
        }
    if isinstance(value, list):
        return [_redact_non_persistable_overrides(item) for item in value]
    if _is_secret_override_value(value):
        return _REDACTED_OVERRIDE_VALUE
    return value


def _omit_packer_build_snapshot_fields(value):
    """Remove raw build override/log fields from one ObjectChange snapshot."""
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key not in {"variable_overrides", "log"}}


def scrub_legacy_proxbox_secrets(apps, schema_editor):
    """Remove secrets from builds and their NetBox changelog snapshots."""
    packer_build = apps.get_model("netbox_packer", "PackerBuild")
    for build in packer_build.objects.only("pk", "variable_overrides", "log").iterator():
        overrides = build.variable_overrides
        contains_unsafe_override = _contains_non_persistable_override(overrides)
        log = build.log
        contains_unsafe_log = isinstance(log, str) and (
            (_LEGACY_PROXBOX_LOG_PREFIX in log and any(marker in log for marker in _LEGACY_UNSAFE_LOG_MARKERS))
            or _is_secret_override_value(log)
        )
        if not contains_unsafe_override and not contains_unsafe_log:
            continue

        update = {}
        if contains_unsafe_override:
            update["variable_overrides"] = _redact_non_persistable_overrides(overrides)
        if contains_unsafe_override or contains_unsafe_log:
            update["log"] = _REDACTED_PACKER_LOG
        packer_build.objects.filter(pk=build.pk).update(**update)

    object_change = apps.get_model("core", "ObjectChange")
    changes = object_change.objects.filter(
        changed_object_type__app_label="netbox_packer",
        changed_object_type__model="packerbuild",
    )
    for change in changes.only("pk", "prechange_data", "postchange_data").iterator():
        update = {}
        for field_name in ("prechange_data", "postchange_data"):
            snapshot = getattr(change, field_name)
            sanitized = _omit_packer_build_snapshot_fields(snapshot)
            if sanitized != snapshot:
                update[field_name] = sanitized
        if update:
            object_change.objects.filter(pk=change.pk).update(**update)


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0023_alter_packerpluginsettings_proxbox_api_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="packerbuild",
            name="proxbox_operation_id",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Durable proxbox-api operation UUID for recovery and operator inspection.",
                max_length=36,
            ),
        ),
        migrations.AlterField(
            model_name="packerbuild",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                    ("recovery_required", "Recovery required"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="packerpluginsettings",
            name="proxbox_writes_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Fail-closed operator gate for template builds and VM provisioning. Enable only "
                    "after post-upgrade validation and the controlled staging canary."
                ),
            ),
        ),
        migrations.RunPython(scrub_legacy_proxbox_secrets, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="packerbuild",
            name="variable_overrides",
            field=models.JSONField(
                blank=True,
                default=dict,
                validators=[netbox_packer.models.validate_packer_build_overrides],
            ),
        ),
    ]
