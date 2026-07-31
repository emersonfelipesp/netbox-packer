"""Render and redact the File Server agent's private package index credentials."""

from __future__ import annotations

import os
from urllib.parse import quote

PACKAGE_READ_USER_ENV = "NMS_FILESERVER_PACKAGE_READ_USER"
PACKAGE_READ_TOKEN_ENV = "NMS_FILESERVER_PACKAGE_READ_TOKEN"  # noqa: S105 - environment variable name, not a secret
PACKAGE_READ_USER_PLACEHOLDER = "__NMS_FILESERVER_PACKAGE_READ_USER__"
PACKAGE_READ_TOKEN_PLACEHOLDER = "__NMS_FILESERVER_PACKAGE_READ_TOKEN__"  # noqa: S105 - placeholder, not a secret
REDACTED_PACKAGE_TOKEN = "[REDACTED_PACKAGE_READ_TOKEN]"
FILESERVER_PIP_CONFIG_PATH = "/etc/nms-fileserver-agent/pip.conf"

# Must match the `TEMPLATE_NAME` seeded by migrations 0014/0017 exactly. Kept as
# its own copy (not imported from a migration module) per Django migration
# immutability convention — migrations must never import from live app code,
# and the converse would tie an already-applied migration's identity to code
# that can change.
FILESERVER_TEMPLATE_NAME = "tpl-fileserver-allinone-ubuntu-2404"


def render_fileserver_package_index(user_data_yaml: str, *, template_name: str) -> str:
    """Replace File Server package-index placeholders from the service environment.

    Cloud-configs without the File Server placeholders pass through unchanged.
    A File Server bake fails closed when either placeholder or required runtime
    secret is missing, preventing pip from silently falling back to public PyPI.

    Substitution is scoped to ``FILESERVER_TEMPLATE_NAME`` specifically: any
    other template's cloud-config that happens to contain the placeholder
    strings (an operator can set arbitrary installer content on any template)
    is rejected rather than silently receiving the real credentials, closing a
    credential exfiltration path. This is only a safe boundary because
    ``PackerTemplate.name`` carries a DB-level ``unique=True`` constraint
    (migration 0018) — without it, an unrelated template could be renamed to
    the exact trusted string and pass this check.
    """
    has_user_placeholder = PACKAGE_READ_USER_PLACEHOLDER in user_data_yaml
    has_token_placeholder = PACKAGE_READ_TOKEN_PLACEHOLDER in user_data_yaml
    if not has_user_placeholder and not has_token_placeholder:
        if FILESERVER_PIP_CONFIG_PATH in user_data_yaml and template_name == FILESERVER_TEMPLATE_NAME:
            raise RuntimeError("File Server package-index credential placeholders are missing")
        return user_data_yaml
    if template_name != FILESERVER_TEMPLATE_NAME:
        raise RuntimeError(
            "File Server package-index credential placeholders were found in the "
            f"cloud-config for template {template_name!r}, which is not the File "
            f"Server golden template ({FILESERVER_TEMPLATE_NAME!r}); refusing to "
            "inject the package-read credential into an unexpected template"
        )
    if not has_user_placeholder or not has_token_placeholder:
        raise RuntimeError("File Server package-index credential placeholders are incomplete")

    package_user = os.environ.get(PACKAGE_READ_USER_ENV, "")
    package_token = os.environ.get(PACKAGE_READ_TOKEN_ENV, "")
    missing = [
        name
        for name, value in (
            (PACKAGE_READ_USER_ENV, package_user),
            (PACKAGE_READ_TOKEN_ENV, package_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "File Server package-index credentials are not configured in the netbox-packer service environment: "
            + ", ".join(missing)
        )

    return user_data_yaml.replace(PACKAGE_READ_USER_PLACEHOLDER, quote(package_user, safe="")).replace(
        PACKAGE_READ_TOKEN_PLACEHOLDER,
        quote(package_token, safe=""),
    )


def redact_fileserver_package_token(value: str) -> str:
    """Remove raw and URL-encoded package tokens before build output is persisted."""
    package_token = os.environ.get(PACKAGE_READ_TOKEN_ENV, "")
    if not package_token:
        return value
    encoded_token = quote(package_token, safe="")
    redacted = value.replace(encoded_token, REDACTED_PACKAGE_TOKEN)
    if encoded_token != package_token:
        redacted = redacted.replace(package_token, REDACTED_PACKAGE_TOKEN)
    return redacted


def sanitized_fileserver_package_error(error: BaseException) -> RuntimeError:
    """Build an unchained exception whose message cannot contain the package token."""
    return RuntimeError(redact_fileserver_package_token(str(error)))
