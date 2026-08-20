"""Render and redact the File Server agent's private package index credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from .models import PackerPluginSettings


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


def render_fileserver_package_index(
    user_data_yaml: str,
    *,
    settings_row: PackerPluginSettings,
    template_name: str,
    is_fileserver_golden_template: bool,
) -> str:
    """Replace File Server package-index placeholders from plugin settings.

    Cloud-configs without the File Server placeholders pass through unchanged.
    A File Server bake fails closed when either placeholder or required runtime
    secret is missing, preventing pip from silently falling back to public PyPI.

    Substitution is scoped to ``is_fileserver_golden_template`` specifically:
    any other template's cloud-config that happens to contain the placeholder
    strings (an operator can set arbitrary installer content on any template)
    is rejected rather than silently receiving the real credentials, closing a
    credential exfiltration path. Authorization is bound to
    ``PackerTemplate.is_fileserver_golden_template`` — an ``editable=False``
    flag only a migration can set (migration 0019) — rather than to
    ``template_name``: name alone is user-editable, and even with the
    DB-level ``unique=True`` constraint on ``name`` (migration 0018) a
    rename-then-reclaim could otherwise hand the trusted name, and the
    credential, to an unrelated template. ``template_name`` is accepted only
    for diagnostic error messages.
    """
    has_user_placeholder = PACKAGE_READ_USER_PLACEHOLDER in user_data_yaml
    has_token_placeholder = PACKAGE_READ_TOKEN_PLACEHOLDER in user_data_yaml
    if not has_user_placeholder and not has_token_placeholder:
        if FILESERVER_PIP_CONFIG_PATH in user_data_yaml and is_fileserver_golden_template:
            raise RuntimeError("File Server package-index credential placeholders are missing")
        return user_data_yaml
    if not is_fileserver_golden_template:
        raise RuntimeError(
            "File Server package-index credential placeholders were found in the "
            f"cloud-config for template {template_name!r}, which is not the File "
            f"Server golden template ({FILESERVER_TEMPLATE_NAME!r}); refusing to "
            "inject the package-read credential into an unexpected template"
        )
    if not has_user_placeholder or not has_token_placeholder:
        raise RuntimeError("File Server package-index credential placeholders are incomplete")

    package_user = settings_row.fileserver_package_read_user
    package_token = settings_row.get_fileserver_package_read_token()
    missing = [
        name
        for name, value in (
            ("PackerPluginSettings.fileserver_package_read_user", package_user),
            ("PackerPluginSettings.fileserver_package_read_token", package_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "File Server package-index credentials are not configured in PackerPluginSettings: " + ", ".join(missing)
        )

    return user_data_yaml.replace(PACKAGE_READ_USER_PLACEHOLDER, quote(package_user, safe="")).replace(
        PACKAGE_READ_TOKEN_PLACEHOLDER,
        quote(package_token, safe=""),
    )


def redact_fileserver_package_token(value: str, package_token: str) -> str:
    """Remove raw and URL-encoded package tokens before build output is persisted."""
    if not package_token:
        return value
    encoded_token = quote(package_token, safe="")
    redacted = value.replace(encoded_token, REDACTED_PACKAGE_TOKEN)
    if encoded_token != package_token:
        redacted = redacted.replace(package_token, REDACTED_PACKAGE_TOKEN)
    return redacted


def sanitized_fileserver_package_error(error: BaseException, package_token: str) -> RuntimeError:
    """Build an unchained exception whose message cannot contain the package token."""
    return RuntimeError(redact_fileserver_package_token(str(error), package_token))
