"""Resolve which proxbox-api backend cloud-init builds talk to, and how they authenticate.

netbox-proxbox owns the proxbox-api linkage: its enabled ``FastAPIEndpoint`` rows
carry the base URL, the API key, and the TLS verification choice. netbox-packer
reuses that linkage by default. ``PackerPluginSettings.proxbox_api_url`` (and the
stored key) remain a supported explicit override, but an override is only honored
when it still matches exactly one enabled netbox-proxbox FastAPI endpoint, so the
override can never point a build at a backend netbox-proxbox does not know about.

Resolution rules (netbox-proxbox is the source of truth):

* A blank URL selects the single enabled netbox-proxbox FastAPI endpoint. Zero or
  several fail closed with a message that names netbox-proxbox as the place to
  configure it.
* A non-blank URL that matches exactly one enabled endpoint selects that endpoint.
  This is the only way an explicit URL is honored, and it disambiguates when
  several endpoints are enabled.
* A non-blank URL that matches no enabled endpoint is treated as stale (the
  plugin has no page to edit or clear it). If exactly one endpoint is enabled it
  is used together with its own key (the stored packer key belonged to the stale
  backend and is ignored too; an endpoint with no key fails closed) and a
  secret-free warning records that the stored value was ignored; with zero or
  several enabled endpoints the build fails closed.
* The stored URL is never used as a transport target unless it matches an enabled
  netbox-proxbox endpoint, so it cannot redirect the backend.
* The stored API key is kept only when the stored URL is blank (the operator keyed
  the default linkage) or when a non-blank stored URL matches the selected
  endpoint (the key was configured for that backend); otherwise the key comes from
  the netbox-proxbox request context. Any non-blank URL that does not match,
  including the value earlier documentation showed as an example, is stale and
  never carries its stored key to the selected endpoint. Secrets are never
  included in messages or in ``repr`` output.

Resolving a linkage is not authorization: the selected Proxmox endpoint is still
authorized and translated to the backend endpoint id by
:mod:`netbox_packer.endpoint_authorization` before dispatch and again at the
final call boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .endpoint_authorization import (
    PackerEndpointAuthorizationError,
    _context_origins,
    _url_origin,
    enabled_fastapi_context_slots,
)

SOURCE_NETBOX_PROXBOX = "netbox-proxbox"
SOURCE_OVERRIDE = "packer setting (matches an enabled netbox-proxbox endpoint)"
SOURCE_STALE_IGNORED = "netbox-proxbox (stale packer setting ignored)"
STALE_SETTING_WARNING = (
    "The stored PackerPluginSettings.proxbox_api_url does not match an enabled netbox-proxbox FastAPI "
    "endpoint and was ignored; using the netbox-proxbox linkage instead."
)
_API_KEY_HEADER = "x-proxbox-api-key"
_CONFIGURE_HINT = "configure the proxbox-api endpoint in netbox-proxbox"


@dataclass(frozen=True)
class ProxboxApiLinkage:
    """Resolved proxbox-api base URL, credential, and TLS choice for one build."""

    base_url: str
    api_key: str = field(repr=False)
    verify_ssl: bool
    source: str
    warning: str = ""


def _context_api_key(context: object) -> str:
    """Return the API key a netbox-proxbox request context would send, or ''."""

    headers = getattr(context, "headers", None) or {}
    for name, value in headers.items():
        if str(name).lower() == _API_KEY_HEADER:
            return str(value or "")
    return ""


def _pick_api_key(configured_key: str, context: object) -> str:
    """Prefer the explicit packer key; otherwise use the netbox-proxbox context key."""

    return configured_key or _context_api_key(context)


def _context_base_url(context: object) -> str:
    """Return the context's preferred base URL (hostname first, then IP address URL)."""

    for attribute in ("http_url", "ip_address_url"):
        candidate = str(getattr(context, attribute, None) or "").strip()
        if candidate:
            return candidate.rstrip("/")
    return ""


def _require_single_context(slots: list[object | None], *, stale_setting: bool = False) -> object:
    """Return the only enabled FastAPI endpoint's context or fail closed naming netbox-proxbox.

    ``slots`` has one entry per enabled endpoint row (None when its context is
    unusable), so an unusable row still counts toward "more than one".
    """

    if not slots:
        raise PackerEndpointAuthorizationError(
            f"No enabled netbox-proxbox FastAPI endpoint is available for cloud-init builds; {_CONFIGURE_HINT}."
        )
    if len(slots) != 1:
        stale = "The stored PackerPluginSettings.proxbox_api_url does not match any of them. " if stale_setting else ""
        raise PackerEndpointAuthorizationError(
            "More than one enabled netbox-proxbox FastAPI endpoint exists, so netbox-packer cannot choose one. "
            f"{stale}Leave a single endpoint enabled in netbox-proxbox, or set "
            "PackerPluginSettings.proxbox_api_url to the origin of the endpoint to use."
        )
    if slots[0] is None:
        raise PackerEndpointAuthorizationError(
            "The enabled netbox-proxbox FastAPI endpoint has no usable request context; "
            "check its target fingerprint and key configuration in netbox-proxbox."
        )
    return slots[0]


def _linkage_from_context(context: object, configured_key: str, *, source: str, warning: str = "") -> ProxboxApiLinkage:
    """Build the linkage for one enabled netbox-proxbox FastAPI context."""

    base_url = _context_base_url(context)
    if not base_url:
        raise PackerEndpointAuthorizationError(
            f"The enabled netbox-proxbox FastAPI endpoint has no usable URL; {_CONFIGURE_HINT}."
        )
    _url_origin(base_url, label="netbox-proxbox FastAPI endpoint URL")
    return ProxboxApiLinkage(
        base_url=base_url,
        api_key=_pick_api_key(configured_key, context),
        verify_ssl=bool(getattr(context, "verify_ssl", True)),
        source=source,
        warning=warning,
    )


def _contexts_matching(configured_url: str, contexts: list[object]) -> list[object]:
    """Return the contexts whose origin equals the stored URL; a malformed URL matches none."""

    try:
        wanted = _url_origin(configured_url, label="proxbox-api URL")
    except PackerEndpointAuthorizationError:
        return []
    return [context for context in contexts if wanted in _context_origins(context)]


def _linkage_from_stale_setting(slots: list[object | None]) -> ProxboxApiLinkage:
    """Use the single enabled endpoint's own URL, key, and TLS choice for a stale stored URL.

    The stored packer key belonged to the stale backend, so it is ignored together
    with the stored URL and never sent to the selected endpoint. If that endpoint
    carries no key of its own the request fails closed.
    """

    context = _require_single_context(slots, stale_setting=True)
    linkage = _linkage_from_context(context, "", source=SOURCE_STALE_IGNORED, warning=STALE_SETTING_WARNING)
    if not linkage.api_key:
        raise PackerEndpointAuthorizationError(
            "The stored PackerPluginSettings.proxbox_api_url (and its API key) were ignored, and the enabled "
            f"netbox-proxbox FastAPI endpoint has no API key of its own; {_CONFIGURE_HINT}."
        )
    return linkage


def _linkage_from_stored_url(configured_url: str, configured_key: str, slots: list[object | None]) -> ProxboxApiLinkage:
    """Honor a stored URL only when it matches an enabled endpoint; otherwise treat it as stale."""

    matches = _contexts_matching(configured_url, [context for context in slots if context is not None])
    if len(matches) > 1:
        raise PackerEndpointAuthorizationError(
            "PackerPluginSettings.proxbox_api_url matches more than one enabled netbox-proxbox FastAPI endpoint."
        )
    if matches:
        # The stored origin equals this endpoint's own origin, so it is a netbox-proxbox target.
        context = matches[0]
        return ProxboxApiLinkage(
            base_url=configured_url.rstrip("/"),
            api_key=_pick_api_key(configured_key, context),
            verify_ssl=bool(getattr(context, "verify_ssl", True)),
            source=SOURCE_OVERRIDE,
        )
    return _linkage_from_stale_setting(slots)


def resolve_proxbox_linkage(configured_url: str | None, configured_key: str | None) -> ProxboxApiLinkage:
    """Resolve the proxbox-api linkage for a cloud-init build, failing closed."""

    url = (configured_url or "").strip()
    key = configured_key or ""
    slots = enabled_fastapi_context_slots()
    if not url:
        return _linkage_from_context(_require_single_context(slots), key, source=SOURCE_NETBOX_PROXBOX)
    return _linkage_from_stored_url(url, key, slots)


__all__ = (
    "ProxboxApiLinkage",
    "SOURCE_NETBOX_PROXBOX",
    "SOURCE_OVERRIDE",
    "SOURCE_STALE_IGNORED",
    "STALE_SETTING_WARNING",
    "resolve_proxbox_linkage",
)
