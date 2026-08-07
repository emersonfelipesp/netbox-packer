"""Small secret-handling helpers shared by models, serializers, and jobs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

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
REDACTED_OVERRIDE_VALUE = "[REDACTED]"
_LEGACY_PROXBOX_LOG_PREFIX = "[INFO] Cloud-init template image build for "
_LEGACY_UNSAFE_LOG_MARKERS = ("[INFO] Base image: ", "[BUILD_SCRIPT]", "[STDOUT]", "[STDERR]")
REDACTED_PACKER_LOG = (
    "[REDACTED] Build log removed because it could contain credentials, signed URLs, "
    "generated scripts, or process output."
)
# Compatibility aliases for callers written before the detector was widened
# from legacy proxbox logs to every secret-shaped Packer log.
REDACTED_LEGACY_PROXBOX_LOG = REDACTED_PACKER_LOG


def is_secret_override_key(value: Any) -> bool:
    """Return whether an override key is reserved or shaped like a credential."""
    normalized = str(value).lower().replace("-", "_")
    return normalized in _NON_PERSISTABLE_OVERRIDE_KEYS or any(part in normalized for part in _SECRET_KEY_PARTS)


def contains_non_persistable_build_override(value: Any) -> bool:
    """Return whether nested overrides contain an ephemeral or secret-shaped value."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if is_secret_override_key(key):
                    return True
                pending.append(nested)
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and any(pattern.search(item) for pattern in _SECRET_VALUE_PATTERNS):
            return True
    return False


def redact_non_persistable_build_overrides(value: Any) -> Any:
    """Copy nested JSON while removing keys and values that can carry credentials."""
    if isinstance(value, Mapping):
        return {
            key: redact_non_persistable_build_overrides(item)
            for key, item in value.items()
            if not is_secret_override_key(key)
        }
    if isinstance(value, list):
        return [redact_non_persistable_build_overrides(item) for item in value]
    if isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        return REDACTED_OVERRIDE_VALUE
    return value


def redact_packer_build_log(value: Any) -> Any:
    """Replace build log text that contains recognizable secret-shaped material."""
    if not isinstance(value, str):
        return value
    unsafe_legacy = _LEGACY_PROXBOX_LOG_PREFIX in value and any(
        marker in value for marker in _LEGACY_UNSAFE_LOG_MARKERS
    )
    if unsafe_legacy or any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        return REDACTED_PACKER_LOG
    return value


def redact_legacy_proxbox_build_log(value: Any) -> Any:
    """Compatibility wrapper for the former legacy-only redactor."""
    return redact_packer_build_log(value)
