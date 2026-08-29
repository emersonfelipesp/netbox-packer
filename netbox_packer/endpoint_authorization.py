"""Fail-closed netbox-proxbox endpoint authorization for template bakes."""

from __future__ import annotations

import ipaddress
from urllib.parse import SplitResult, urlsplit

from django.apps import apps


class PackerEndpointAuthorizationError(RuntimeError):
    """The selected endpoint could not be proven authorized for a bake."""


class PackerTemplateEndpointAuthorization:
    """One authorized NetBox endpoint and its exact proxbox-api row id."""

    __slots__ = ("backend_endpoint_id", "endpoint")

    def __init__(self, endpoint: object, backend_endpoint_id: int) -> None:
        self.endpoint = endpoint
        self.backend_endpoint_id = backend_endpoint_id


def _split_absolute_http_url(value: str, *, label: str) -> SplitResult:
    raw = str(value or "").strip()
    if not raw:
        raise PackerEndpointAuthorizationError(f"{label} is required.")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise PackerEndpointAuthorizationError(f"{label} is malformed: {exc}.") from None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise PackerEndpointAuthorizationError(f"{label} must be an absolute HTTP or HTTPS URL.")
    if parsed.username is not None or parsed.password is not None:
        raise PackerEndpointAuthorizationError(f"{label} must not contain credentials.")
    if parsed.path not in {"", "/"}:
        raise PackerEndpointAuthorizationError(f"{label} must not contain a path.")
    if parsed.query or parsed.fragment:
        raise PackerEndpointAuthorizationError(f"{label} must not contain a query string or fragment.")
    return parsed


def _normalize_host(value: object) -> str | None:
    raw = getattr(value, "address", value)
    host = str(raw or "").strip().rstrip(".").lower()
    if not host:
        return None
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        try:
            return ipaddress.ip_interface(host).ip.compressed
        except ValueError:
            return host


def _url_target(value: str, *, label: str) -> tuple[str, int]:
    parsed = _split_absolute_http_url(value, label=label)
    host = _normalize_host(parsed.hostname)
    if host is None:
        raise PackerEndpointAuthorizationError(f"{label} has no usable host.")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return host, port


def _url_origin(value: str, *, label: str) -> tuple[str, str, int]:
    parsed = _split_absolute_http_url(value, label=label)
    host, port = _url_target(value, label=label)
    return parsed.scheme.lower(), host, port


def _endpoint_targets(endpoint: object) -> set[tuple[str, int]]:
    try:
        port = int(endpoint.port)
    except (AttributeError, TypeError, ValueError):
        return set()
    if not 1 <= port <= 65535:
        return set()

    hosts = {
        host
        for host in (
            _normalize_host(getattr(endpoint, "domain", None)),
            _normalize_host(getattr(endpoint, "ip_address", None)),
        )
        if host is not None
    }
    return {(host, port) for host in hosts}


def resolve_netbox_proxmox_endpoint(endpoint_url: str) -> object:
    """Resolve a URL to exactly one netbox-proxbox endpoint by host and port."""

    wanted = _url_target(endpoint_url, label="Proxmox endpoint URL")
    try:
        endpoint_model = apps.get_model("netbox_proxbox", "ProxmoxEndpoint")
    except LookupError:
        raise PackerEndpointAuthorizationError(
            "netbox-proxbox is not installed; template builds require its endpoint authorization."
        ) from None

    matches = [endpoint for endpoint in endpoint_model.objects.all() if wanted in _endpoint_targets(endpoint)]
    if not matches:
        raise PackerEndpointAuthorizationError(
            "The selected Proxmox endpoint URL does not match any netbox-proxbox endpoint."
        )
    if len(matches) != 1:
        raise PackerEndpointAuthorizationError(
            "The selected Proxmox endpoint URL matches more than one netbox-proxbox endpoint."
        )
    return matches[0]


def _resolve_backend_authorization(
    endpoint: object,
    proxbox_api_url: str,
) -> PackerTemplateEndpointAuthorization:
    """Translate one plugin endpoint through the exact configured backend."""

    wanted_origin = _url_origin(proxbox_api_url, label="proxbox-api URL")
    try:
        fastapi_model = apps.get_model("netbox_proxbox", "FastAPIEndpoint")
    except LookupError:
        raise PackerEndpointAuthorizationError("netbox-proxbox FastAPI endpoint inventory is unavailable.") from None

    from netbox_proxbox.services.backend_context import get_fastapi_request_context
    from netbox_proxbox.views.backend_sync import resolve_backend_endpoint_id

    contexts: list[object] = []
    for fastapi_endpoint in fastapi_model.objects.filter(enabled=True):
        try:
            context = get_fastapi_request_context(endpoint_id=fastapi_endpoint.pk)
        except Exception as exc:
            raise PackerEndpointAuthorizationError(
                "Could not resolve the configured netbox-proxbox FastAPI endpoint context."
            ) from exc
        if context is None:
            continue
        context_origins: set[tuple[str, str, int]] = set()
        for candidate in (
            getattr(context, "http_url", None),
            getattr(context, "ip_address_url", None),
        ):
            if not candidate:
                continue
            try:
                context_origins.add(_url_origin(candidate, label="configured proxbox-api URL"))
            except PackerEndpointAuthorizationError:
                continue
        if wanted_origin in context_origins:
            contexts.append(context)

    if not contexts:
        raise PackerEndpointAuthorizationError(
            "PackerPluginSettings.proxbox_api_url does not match an enabled netbox-proxbox FastAPI endpoint."
        )
    if len(contexts) != 1:
        raise PackerEndpointAuthorizationError(
            "PackerPluginSettings.proxbox_api_url matches more than one enabled netbox-proxbox FastAPI endpoint."
        )

    context = contexts[0]
    try:
        backend_endpoint_id, error = resolve_backend_endpoint_id(
            endpoint,
            base_url=proxbox_api_url.rstrip("/"),
            auth_headers=getattr(context, "headers", None) or {},
            backend_verify_ssl=bool(getattr(context, "verify_ssl", True)),
        )
    except Exception as exc:
        raise PackerEndpointAuthorizationError(
            "The selected endpoint could not be resolved on the configured proxbox-api backend."
        ) from exc
    if backend_endpoint_id is None:
        raise PackerEndpointAuthorizationError(
            error or "The selected endpoint could not be resolved on the configured proxbox-api backend."
        )
    return PackerTemplateEndpointAuthorization(endpoint, int(backend_endpoint_id))


def authorize_packer_template_build(
    endpoint_url: str,
    proxbox_api_url: str,
) -> PackerTemplateEndpointAuthorization:
    """Require both local gates and resolve the exact proxbox-api endpoint id."""

    endpoint = resolve_netbox_proxmox_endpoint(endpoint_url)
    if not bool(getattr(endpoint, "enabled", False)):
        raise PackerEndpointAuthorizationError("The selected netbox-proxbox ProxmoxEndpoint is disabled.")
    if not bool(getattr(endpoint, "allow_writes", False)):
        raise PackerEndpointAuthorizationError(
            "The selected netbox-proxbox ProxmoxEndpoint must have allow_writes enabled."
        )
    if not bool(getattr(endpoint, "allow_packer_template_builds", False)):
        raise PackerEndpointAuthorizationError(
            "The selected netbox-proxbox ProxmoxEndpoint must have allow_packer_template_builds enabled."
        )
    return _resolve_backend_authorization(endpoint, proxbox_api_url)


__all__ = (
    "PackerEndpointAuthorizationError",
    "PackerTemplateEndpointAuthorization",
    "authorize_packer_template_build",
    "resolve_netbox_proxmox_endpoint",
)
