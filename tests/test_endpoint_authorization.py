"""Fail-closed endpoint authorization for cloud-init template bakes."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "netbox_packer" / "endpoint_authorization.py"
SPEC = importlib.util.spec_from_file_location(
    "netbox_packer_endpoint_authorization_test",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
endpoint_authorization = importlib.util.module_from_spec(SPEC)
_saved_django = sys.modules.get("django")
_saved_django_apps = sys.modules.get("django.apps")
_django = types.ModuleType("django")
_django_apps = types.ModuleType("django.apps")
_django_apps.apps = SimpleNamespace()
sys.modules["django"] = _django
sys.modules["django.apps"] = _django_apps
try:
    SPEC.loader.exec_module(endpoint_authorization)
finally:
    if _saved_django is None:
        sys.modules.pop("django", None)
    else:
        sys.modules["django"] = _saved_django
    if _saved_django_apps is None:
        sys.modules.pop("django.apps", None)
    else:
        sys.modules["django.apps"] = _saved_django_apps


class _EndpointManager:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class _EnabledManager:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def filter(self, **filters: object) -> list[object]:
        assert filters == {"enabled": True}
        return self.rows


class _Apps:
    def __init__(self, rows: list[object]) -> None:
        self.endpoint_model = type(
            "ProxmoxEndpoint",
            (),
            {"objects": _EndpointManager(rows)},
        )

    def get_model(self, app_label: str, model_name: str) -> object:
        assert (app_label, model_name) == ("netbox_proxbox", "ProxmoxEndpoint")
        return self.endpoint_model


def _endpoint(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "pk": 7,
        "name": "PVE",
        "domain": "pve.example.com",
        "ip_address": SimpleNamespace(address="192.0.2.10/24"),
        "port": 8006,
        "enabled": True,
        "allow_writes": True,
        "allow_packer_template_builds": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_backend_resolution_stubs(
    monkeypatch,
    *,
    contexts: dict[int, object],
    resolver: object,
) -> None:
    fastapi_rows = [SimpleNamespace(pk=pk) for pk in contexts]
    fastapi_model = type(
        "FastAPIEndpoint",
        (),
        {"objects": _EnabledManager(fastapi_rows)},
    )
    monkeypatch.setattr(
        endpoint_authorization,
        "apps",
        SimpleNamespace(get_model=lambda *_args: fastapi_model),
    )

    backend_context = types.ModuleType("netbox_proxbox.services.backend_context")
    backend_context.get_fastapi_request_context = lambda endpoint_id: contexts[endpoint_id]
    backend_sync = types.ModuleType("netbox_proxbox.views.backend_sync")
    backend_sync.resolve_backend_endpoint_id = resolver
    monkeypatch.setitem(sys.modules, "netbox_proxbox", types.ModuleType("netbox_proxbox"))
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.services",
        types.ModuleType("netbox_proxbox.services"),
    )
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.services.backend_context",
        backend_context,
    )
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.views",
        types.ModuleType("netbox_proxbox.views"),
    )
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.views.backend_sync",
        backend_sync,
    )


def test_resolves_exact_normalized_host_and_port(monkeypatch) -> None:
    expected = _endpoint(domain="PVE.EXAMPLE.COM.")
    monkeypatch.setattr(endpoint_authorization, "apps", _Apps([expected]))

    resolved = endpoint_authorization.resolve_netbox_proxmox_endpoint("https://pve.example.com:8006/")

    assert resolved is expected


def test_resolves_exact_modeled_ip_address_when_domain_is_also_present(monkeypatch) -> None:
    expected = _endpoint()
    monkeypatch.setattr(endpoint_authorization, "apps", _Apps([expected]))

    resolved = endpoint_authorization.resolve_netbox_proxmox_endpoint("https://192.0.2.10:8006/")

    assert resolved is expected


@pytest.mark.parametrize(
    ("endpoint_url", "message"),
    (
        ("", "required"),
        ("pve.example.com:8006", "absolute HTTP"),
        ("https://user:secret@pve.example.com:8006", "credentials"),
        ("https://pve.example.com:8006/api", "path"),
        ("https://pve.example.com:9000", "does not match"),
    ),
)
def test_missing_malformed_and_unmatched_urls_fail_closed(
    monkeypatch,
    endpoint_url: str,
    message: str,
) -> None:
    monkeypatch.setattr(endpoint_authorization, "apps", _Apps([_endpoint()]))

    with pytest.raises(
        endpoint_authorization.PackerEndpointAuthorizationError,
        match=message,
    ):
        endpoint_authorization.resolve_netbox_proxmox_endpoint(endpoint_url)


def test_ambiguous_endpoint_match_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        endpoint_authorization,
        "apps",
        _Apps([_endpoint(pk=7), _endpoint(pk=8)]),
    )

    with pytest.raises(
        endpoint_authorization.PackerEndpointAuthorizationError,
        match="more than one",
    ):
        endpoint_authorization.resolve_netbox_proxmox_endpoint("https://pve.example.com:8006")


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"enabled": False}, "disabled"),
        ({"allow_writes": False}, "allow_writes"),
        ({"allow_packer_template_builds": False}, "allow_packer_template_builds"),
    ),
)
def test_endpoint_state_and_both_authorizations_are_required(
    monkeypatch,
    overrides: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setattr(
        endpoint_authorization,
        "apps",
        _Apps([_endpoint(**overrides)]),
    )

    with pytest.raises(
        endpoint_authorization.PackerEndpointAuthorizationError,
        match=message,
    ):
        endpoint_authorization.authorize_packer_template_build(
            "https://pve.example.com:8006",
            "https://proxbox-api.example.com",
        )


def test_authorization_returns_exact_backend_id(monkeypatch) -> None:
    endpoint = _endpoint()
    monkeypatch.setattr(endpoint_authorization, "apps", _Apps([endpoint]))
    resolver = SimpleNamespace(endpoint=endpoint, backend_endpoint_id=91)
    monkeypatch.setattr(
        endpoint_authorization,
        "_resolve_backend_authorization",
        lambda _selected, _api_url: resolver,
    )

    authorization = endpoint_authorization.authorize_packer_template_build(
        "https://pve.example.com:8006",
        "https://proxbox-api.example.com",
    )

    assert authorization.endpoint is endpoint
    assert authorization.backend_endpoint_id == 91


def test_backend_authorization_uses_exact_configured_fastapi_context(
    monkeypatch,
) -> None:
    endpoint = _endpoint()
    fastapi_endpoint = SimpleNamespace(pk=12)
    fastapi_model = type(
        "FastAPIEndpoint",
        (),
        {"objects": _EnabledManager([fastapi_endpoint])},
    )
    monkeypatch.setattr(
        endpoint_authorization,
        "apps",
        SimpleNamespace(
            get_model=lambda app_label, model_name: (
                fastapi_model if (app_label, model_name) == ("netbox_proxbox", "FastAPIEndpoint") else None
            )
        ),
    )

    backend_context = types.ModuleType("netbox_proxbox.services.backend_context")
    backend_context.get_fastapi_request_context = lambda endpoint_id: (
        SimpleNamespace(
            http_url="https://proxbox-api.example.com/",
            ip_address_url="https://192.0.2.20/",
            headers={"Authorization": "test"},
            verify_ssl=False,
        )
        if endpoint_id == 12
        else None
    )
    captured: dict[str, object] = {}

    def resolve_backend_endpoint_id(selected: object, **kwargs: object):
        captured["endpoint"] = selected
        captured.update(kwargs)
        return 91, None

    backend_sync = types.ModuleType("netbox_proxbox.views.backend_sync")
    backend_sync.resolve_backend_endpoint_id = resolve_backend_endpoint_id
    monkeypatch.setitem(sys.modules, "netbox_proxbox", types.ModuleType("netbox_proxbox"))
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.services",
        types.ModuleType("netbox_proxbox.services"),
    )
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.services.backend_context",
        backend_context,
    )
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.views",
        types.ModuleType("netbox_proxbox.views"),
    )
    monkeypatch.setitem(
        sys.modules,
        "netbox_proxbox.views.backend_sync",
        backend_sync,
    )

    authorization = endpoint_authorization._resolve_backend_authorization(
        endpoint,
        "https://PROXBOX-API.example.com/",
    )

    assert authorization.endpoint is endpoint
    assert authorization.backend_endpoint_id == 91
    assert captured == {
        "endpoint": endpoint,
        "base_url": "https://PROXBOX-API.example.com",
        "auth_headers": {"Authorization": "test"},
        "backend_verify_ssl": False,
    }


@pytest.mark.parametrize(
    ("contexts", "message"),
    (
        ({}, "does not match"),
        (
            {
                1: SimpleNamespace(
                    http_url="not-an-absolute-url",
                    ip_address_url=None,
                    headers={},
                    verify_ssl=True,
                )
            },
            "does not match",
        ),
        (
            {
                1: SimpleNamespace(
                    http_url="https://proxbox-api.example.com",
                    ip_address_url=None,
                    headers={},
                    verify_ssl=True,
                ),
                2: SimpleNamespace(
                    http_url="https://proxbox-api.example.com/",
                    ip_address_url=None,
                    headers={},
                    verify_ssl=True,
                ),
            },
            "more than one",
        ),
    ),
)
def test_backend_context_missing_malformed_and_ambiguous_fail_closed(
    monkeypatch,
    contexts: dict[int, object],
    message: str,
) -> None:
    _install_backend_resolution_stubs(
        monkeypatch,
        contexts=contexts,
        resolver=lambda *_args, **_kwargs: (91, None),
    )

    with pytest.raises(
        endpoint_authorization.PackerEndpointAuthorizationError,
        match=message,
    ):
        endpoint_authorization._resolve_backend_authorization(
            _endpoint(),
            "https://proxbox-api.example.com",
        )


@pytest.mark.parametrize(
    "resolver",
    (
        lambda *_args, **_kwargs: (None, "backend endpoint missing"),
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("backend unavailable")),
    ),
)
def test_backend_endpoint_resolver_failures_are_typed_and_fail_closed(
    monkeypatch,
    resolver,
) -> None:
    context = SimpleNamespace(
        http_url="https://proxbox-api.example.com",
        ip_address_url=None,
        headers={},
        verify_ssl=True,
    )
    _install_backend_resolution_stubs(
        monkeypatch,
        contexts={1: context},
        resolver=resolver,
    )

    with pytest.raises(endpoint_authorization.PackerEndpointAuthorizationError):
        endpoint_authorization._resolve_backend_authorization(
            _endpoint(),
            "https://proxbox-api.example.com",
        )


def test_backend_context_resolution_exception_is_typed(monkeypatch) -> None:
    context = SimpleNamespace(
        http_url="https://proxbox-api.example.com",
        ip_address_url=None,
        headers={},
        verify_ssl=True,
    )
    _install_backend_resolution_stubs(
        monkeypatch,
        contexts={1: context},
        resolver=lambda *_args, **_kwargs: (91, None),
    )
    backend_context = sys.modules["netbox_proxbox.services.backend_context"]
    backend_context.get_fastapi_request_context = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("context unavailable")
    )

    with pytest.raises(
        endpoint_authorization.PackerEndpointAuthorizationError,
        match="Could not resolve",
    ):
        endpoint_authorization._resolve_backend_authorization(
            _endpoint(),
            "https://proxbox-api.example.com",
        )
