"""The proxbox-api linkage is reused from netbox-proxbox unless explicitly overridden."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "netbox_packer"
ISOLATED_PREFIXES = ("django", "netbox_proxbox", "netbox_packer")

API_KEY_HEADER = "X-Proxbox-API-Key"
CONTEXT_KEY = "context-key-value"
SETTING_KEY = "setting-key-value"


def _url(host: str, port: int = 8000, scheme: str = "https") -> str:
    return f"{scheme}://{host}:{port}"


BACKEND_A = _url("backend-a.example.invalid")
BACKEND_B = _url("backend-b.example.invalid")


@pytest.fixture
def linkage_env(monkeypatch):
    """Load the linkage module against stubbed Django apps and netbox-proxbox contexts."""

    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in ISOLATED_PREFIXES)
    }
    for name in saved:
        sys.modules.pop(name, None)

    package = types.ModuleType("netbox_packer")
    package.__path__ = [str(PKG)]
    sys.modules["netbox_packer"] = package

    state = SimpleNamespace(contexts={}, model_available=True)

    class Rows:
        def filter(self, **filters):
            assert filters == {"enabled": True}
            return [SimpleNamespace(pk=pk) for pk in state.contexts]

    fastapi_model = type("FastAPIEndpoint", (), {"objects": Rows()})

    def get_model(app_label, model_name):
        assert (app_label, model_name) == ("netbox_proxbox", "FastAPIEndpoint")
        if not state.model_available:
            raise LookupError(model_name)
        return fastapi_model

    django_apps = types.ModuleType("django.apps")
    django_apps.apps = SimpleNamespace(get_model=get_model)
    backend_context = types.ModuleType("netbox_proxbox.services.backend_context")
    backend_context.get_fastapi_request_context = lambda endpoint_id: state.contexts[endpoint_id]
    for name, module in (
        ("django", types.ModuleType("django")),
        ("django.apps", django_apps),
        ("netbox_proxbox", types.ModuleType("netbox_proxbox")),
        ("netbox_proxbox.services", types.ModuleType("netbox_proxbox.services")),
        ("netbox_proxbox.services.backend_context", backend_context),
    ):
        sys.modules[name] = module

    state.linkage = importlib.import_module("netbox_packer.proxbox_linkage")
    state.authorization = importlib.import_module("netbox_packer.endpoint_authorization")

    def context(http_url=BACKEND_A, ip_address_url=None, headers=None, verify_ssl=True):
        if headers is None:
            headers = {API_KEY_HEADER: CONTEXT_KEY}
        return SimpleNamespace(
            http_url=http_url,
            ip_address_url=ip_address_url,
            headers=headers,
            verify_ssl=verify_ssl,
        )

    state.context = context
    yield state

    for name in list(sys.modules):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in ISOLATED_PREFIXES):
            sys.modules.pop(name, None)
    sys.modules.update(saved)


# The origin earlier documentation showed as an example. It is just another stored
# value: it is honored only when it matches an enabled netbox-proxbox endpoint.
LEGACY_EXAMPLE = _url("legacy-backend.example.invalid", 8000, "http")


# ---------------------------------------------------------------------------
# Default linkage: a blank stored URL reuses the single netbox-proxbox endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("configured", ("", "   ", None))
def test_a_blank_stored_url_uses_the_single_netbox_proxbox_endpoint(linkage_env, configured) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A, verify_ssl=False)}

    result = env.linkage.resolve_proxbox_linkage(configured, "")

    assert result.base_url == BACKEND_A
    assert result.source == env.linkage.SOURCE_NETBOX_PROXBOX
    assert result.verify_ssl is False
    assert result.api_key == CONTEXT_KEY
    assert result.warning == ""


@pytest.mark.parametrize("variant", ("plain", "slash", "upper"))
def test_the_legacy_example_url_is_stale_unless_it_matches_the_selected_endpoint(linkage_env, variant) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}
    configured = {"plain": LEGACY_EXAMPLE, "slash": LEGACY_EXAMPLE + "/", "upper": LEGACY_EXAMPLE.upper()}[variant]

    result = env.linkage.resolve_proxbox_linkage(configured, STALE_BACKEND_KEY)

    assert result.base_url == BACKEND_A
    assert result.source == env.linkage.SOURCE_STALE_IGNORED
    assert result.warning == env.linkage.STALE_SETTING_WARNING
    assert result.api_key == CONTEXT_KEY


def test_default_linkage_prefers_the_hostname_url_over_the_ip_url(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A + "/", ip_address_url=BACKEND_B)}

    assert env.linkage.resolve_proxbox_linkage("", "").base_url == BACKEND_A


def test_default_linkage_falls_back_to_the_ip_url(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=None, ip_address_url=BACKEND_B)}

    assert env.linkage.resolve_proxbox_linkage("", "").base_url == BACKEND_B


def test_default_linkage_without_any_usable_url_fails_closed(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url="", ip_address_url=None)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError, match="no usable URL"):
        env.linkage.resolve_proxbox_linkage("", "")


@pytest.mark.parametrize("configured", ("", None))
def test_zero_enabled_endpoints_fail_closed_and_name_netbox_proxbox(linkage_env, configured) -> None:
    env = linkage_env
    env.contexts = {}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(configured, SETTING_KEY)

    assert "netbox-proxbox" in str(excinfo.value)
    assert "No enabled" in str(excinfo.value)


def test_two_enabled_endpoints_fail_closed_when_the_default_is_used(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A), 2: env.context(http_url=BACKEND_B)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage("", "")

    assert "More than one enabled netbox-proxbox FastAPI endpoint" in str(excinfo.value)
    assert "netbox-proxbox" in str(excinfo.value)


def test_a_missing_fastapi_inventory_fails_closed(linkage_env) -> None:
    env = linkage_env
    env.model_available = False

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError, match="inventory is unavailable"):
        env.linkage.resolve_proxbox_linkage("", "")


# ---------------------------------------------------------------------------
# Explicit override: still supported, still must match an enabled endpoint.
# ---------------------------------------------------------------------------


def test_explicit_override_matching_one_endpoint_is_used(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A), 2: env.context(http_url=BACKEND_B, verify_ssl=False)}

    result = env.linkage.resolve_proxbox_linkage(BACKEND_B + "/", "")

    assert result.base_url == BACKEND_B
    assert result.source == env.linkage.SOURCE_OVERRIDE
    assert result.verify_ssl is False


def test_explicit_override_may_match_by_the_ip_address_url(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A, ip_address_url=BACKEND_B)}

    assert env.linkage.resolve_proxbox_linkage(BACKEND_B, "").base_url == BACKEND_B


STALE_LOOPBACK = _url("127.0.0.1", 8800, "http")


def test_stale_value_with_a_single_enabled_endpoint_is_ignored_with_a_warning(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A, verify_ssl=False)}

    result = env.linkage.resolve_proxbox_linkage(STALE_LOOPBACK, "")

    # The stale value is never used as a transport target.
    assert result.base_url == BACKEND_A
    assert STALE_LOOPBACK not in result.base_url
    assert result.source == env.linkage.SOURCE_STALE_IGNORED
    assert result.warning == env.linkage.STALE_SETTING_WARNING
    assert result.verify_ssl is False
    assert result.api_key == CONTEXT_KEY
    assert STALE_LOOPBACK not in result.warning


STALE_BACKEND_KEY = "stale-backend-a-key"
SELECTED_BACKEND_KEY = "selected-backend-b-key"


def _stale_backend_env(env, selected_headers=None):
    """Backend A (stored URL and key) is gone; only backend B is enabled."""

    headers = {API_KEY_HEADER: SELECTED_BACKEND_KEY} if selected_headers is None else selected_headers
    env.contexts = {1: env.context(http_url=BACKEND_B, headers=headers, verify_ssl=False)}


def test_a_stale_value_never_carries_its_key_to_the_selected_backend(linkage_env) -> None:
    env = linkage_env
    _stale_backend_env(env)

    result = env.linkage.resolve_proxbox_linkage(BACKEND_A, STALE_BACKEND_KEY)

    assert result.base_url == BACKEND_B
    assert result.api_key == SELECTED_BACKEND_KEY
    assert result.source == env.linkage.SOURCE_STALE_IGNORED
    assert result.verify_ssl is False
    for text in (repr(result), result.warning, result.base_url):
        assert STALE_BACKEND_KEY not in text


def test_a_stale_value_fails_closed_when_the_selected_backend_has_no_key(linkage_env) -> None:
    env = linkage_env
    _stale_backend_env(env, selected_headers={})

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(BACKEND_A, STALE_BACKEND_KEY)

    message = str(excinfo.value)
    assert "no API key of its own" in message
    assert "netbox-proxbox" in message
    assert STALE_BACKEND_KEY not in message


def test_an_explicit_match_may_use_the_key_stored_for_that_url(linkage_env) -> None:
    env = linkage_env
    env.contexts = {
        1: env.context(http_url=BACKEND_A, headers={API_KEY_HEADER: "context-key-a"}),
        2: env.context(http_url=BACKEND_B, headers={API_KEY_HEADER: SELECTED_BACKEND_KEY}),
    }

    result = env.linkage.resolve_proxbox_linkage(BACKEND_A, STALE_BACKEND_KEY)

    assert result.base_url == BACKEND_A
    assert result.api_key == STALE_BACKEND_KEY


def test_a_blank_stored_url_still_lets_the_stored_packer_key_override(linkage_env) -> None:
    env = linkage_env
    _stale_backend_env(env)

    assert env.linkage.resolve_proxbox_linkage("", "operator-key").api_key == "operator-key"
    assert env.linkage.resolve_proxbox_linkage("", "").api_key == SELECTED_BACKEND_KEY


def test_the_legacy_urls_key_never_reaches_a_different_backend(linkage_env) -> None:
    env = linkage_env
    _stale_backend_env(env)

    result = env.linkage.resolve_proxbox_linkage(LEGACY_EXAMPLE, STALE_BACKEND_KEY)

    assert result.base_url == BACKEND_B
    assert result.api_key == SELECTED_BACKEND_KEY
    assert result.source == env.linkage.SOURCE_STALE_IGNORED
    assert result.warning == env.linkage.STALE_SETTING_WARNING
    for text in (repr(result), result.warning, result.base_url):
        assert STALE_BACKEND_KEY not in text


def test_the_legacy_url_fails_closed_when_the_selected_backend_has_no_key(linkage_env) -> None:
    env = linkage_env
    _stale_backend_env(env, selected_headers={})

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(LEGACY_EXAMPLE, STALE_BACKEND_KEY)

    assert "no API key of its own" in str(excinfo.value)
    assert STALE_BACKEND_KEY not in str(excinfo.value)


def test_the_legacy_url_keeps_its_key_only_when_it_matches_the_selected_endpoint(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=LEGACY_EXAMPLE, headers={API_KEY_HEADER: SELECTED_BACKEND_KEY})}

    result = env.linkage.resolve_proxbox_linkage(LEGACY_EXAMPLE, STALE_BACKEND_KEY)

    assert result.source == env.linkage.SOURCE_OVERRIDE
    assert result.api_key == STALE_BACKEND_KEY


def _run_cloud_build(env, monkeypatch, settings_row):
    """Run _run_proxbox_cloud_build up to the proxbox-api call and return its kwargs."""

    jobs = _import_jobs(monkeypatch)
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerPluginSettings = type("PackerPluginSettings", (), {"get_solo": staticmethod(lambda: settings_row)})
    models_mod.PackerTemplate = type("PackerTemplate", (), {})
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models_mod)

    class StubApiError(Exception):
        pass

    call = Mock(side_effect=StubApiError("stop after capturing the request"))
    client_mod = types.ModuleType("netbox_packer.proxbox_client")
    client_mod.ProxboxApiError = StubApiError
    client_mod.call_proxbox_build = call
    monkeypatch.setitem(sys.modules, "netbox_packer.proxbox_client", client_mod)
    jobs._inject_monitoring_agents = Mock(return_value="#cloud-config\n")
    jobs._authorize_selected_endpoint = Mock(return_value=SimpleNamespace(backend_endpoint_id=17))
    template = SimpleNamespace(
        pk=1,
        name="stale-key-template",
        installer_config=SimpleNamespace(
            name="cfg", version="1", installer_type="cloud_config", content="#cloud-config\n"
        ),
        storage_pool="local",
        proxmox_node="node1",
        proxmox_template_id=9000,
        os_family="ubuntu",
        os_version="24.04",
        base_image_url="https://images.example.invalid/base.img",
        base_image_sha256="a" * 64,
        install_qemu_guest_agent=False,
        install_zabbix_agent2=False,
        zabbix_server="",
        provisions_service="",
    )
    build = SimpleNamespace(variable_overrides={"target_node": "node1"}, log="", save=Mock())

    with pytest.raises(RuntimeError):
        jobs.PackerBuildJob()._run_proxbox_cloud_build(build, template, "https://pve.example.invalid:8006", "node1", 60)
    return call.call_args.kwargs, build, jobs._authorize_selected_endpoint


@pytest.mark.parametrize("stored_url", (BACKEND_A, LEGACY_EXAMPLE))
def test_the_build_call_receives_the_selected_backends_key_not_the_stale_one(
    linkage_env, monkeypatch, stored_url
) -> None:
    env = linkage_env
    _stale_backend_env(env)
    settings_row = SimpleNamespace(proxbox_api_url=stored_url, get_proxbox_api_key=lambda: STALE_BACKEND_KEY)

    kwargs, build, authorize = _run_cloud_build(env, monkeypatch, settings_row)

    assert kwargs["proxbox_api_url"] == BACKEND_B
    assert kwargs["proxbox_api_key"] == SELECTED_BACKEND_KEY
    assert kwargs["verify_ssl"] is False
    authorize.assert_called_once_with("https://pve.example.invalid:8006", BACKEND_B)
    assert STALE_BACKEND_KEY not in build.log
    assert SELECTED_BACKEND_KEY not in build.log
    assert "[WARNING]" in build.log


@pytest.mark.parametrize("stored_url", (BACKEND_A, LEGACY_EXAMPLE))
def test_the_provision_call_receives_the_selected_backends_key_not_the_stale_one(
    linkage_env, monkeypatch, stored_url
) -> None:
    env = linkage_env
    _stale_backend_env(env)
    settings_row = SimpleNamespace(proxbox_api_url=stored_url, get_proxbox_api_key=lambda: STALE_BACKEND_KEY)

    messages = SimpleNamespace(success=Mock(), error=Mock())
    contrib = types.ModuleType("django.contrib")
    contrib.messages = messages
    exceptions = types.ModuleType("django.core.exceptions")
    exceptions.PermissionDenied = type("PermissionDenied", (Exception,), {})
    shortcuts = types.ModuleType("django.shortcuts")
    shortcuts.get_object_or_404 = lambda *_args, **_kwargs: template
    shortcuts.redirect = lambda target: {"redirect": target}
    generic = types.ModuleType("netbox.views.generic")
    for name in ("ObjectView", "ObjectListView", "ObjectEditView", "ObjectDeleteView"):
        setattr(generic, name, type(name, (), {}))
    netbox_views = types.ModuleType("netbox.views")
    netbox_views.generic = generic
    utilities_views = types.ModuleType("utilities.views")
    utilities_views.register_model_view = lambda *_args, **_kwargs: lambda cls: cls

    template = SimpleNamespace(pk=3, name="tpl", get_absolute_url=lambda: "/t/3/")
    payload = {"new_vmid": 201}

    class Form:
        errors = {}

        def __init__(self, *_args, **_kwargs):
            pass

        def is_valid(self):
            return True

        def proxbox_payload(self, _template):
            return payload

    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerPluginSettings = type("PackerPluginSettings", (), {"get_solo": staticmethod(lambda: settings_row)})

    class Chain:
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: self

    for name in ("PackerTemplate", "PackerInstallerConfig", "PackerBuild", "PackerBuildTarget"):
        setattr(models_mod, name, type(name, (), {"objects": Chain()}))
    stub_modules = {
        "django.contrib": contrib,
        "django.contrib.messages": messages,
        "django.core": types.ModuleType("django.core"),
        "django.core.exceptions": exceptions,
        "django.shortcuts": shortcuts,
        "netbox.views": netbox_views,
        "netbox.views.generic": generic,
        "utilities": types.ModuleType("utilities"),
        "utilities.views": utilities_views,
        "netbox_packer.models": models_mod,
    }
    for rel in ("filtersets", "forms", "tables"):
        module = types.ModuleType(f"netbox_packer.{rel}")
        module.__getattr__ = lambda _name: object
        stub_modules[f"netbox_packer.{rel}"] = module
    stub_modules["netbox_packer.forms"].PackerTemplateCreateInstanceForm = Form
    for name, module in stub_modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    provision = Mock(return_value={"status": "queued"})
    client_mod = types.ModuleType("netbox_packer.proxbox_client")
    client_mod.ProxboxApiError = type("ProxboxApiError", (Exception,), {})
    client_mod.call_proxbox_vm_provision = provision
    monkeypatch.setitem(sys.modules, "netbox_packer.proxbox_client", client_mod)
    views = importlib.import_module("netbox_packer.views")
    request = SimpleNamespace(
        user=SimpleNamespace(has_perm=lambda _perm: True),
        POST={},
    )

    views.PackerTemplateCreateInstanceView().post(request, pk=3)

    kwargs = provision.call_args.kwargs
    assert kwargs["proxbox_api_url"] == BACKEND_B
    assert kwargs["proxbox_api_key"] == SELECTED_BACKEND_KEY
    assert kwargs["verify_ssl"] is False
    assert kwargs["new_vmid"] == 201
    messages.error.assert_not_called()


def test_stale_value_with_two_enabled_endpoints_fails_closed(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A), 2: env.context(http_url=BACKEND_B)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(STALE_LOOPBACK, SETTING_KEY)

    message = str(excinfo.value)
    assert "More than one enabled netbox-proxbox FastAPI endpoint" in message
    assert "does not match any of them" in message
    assert "netbox-proxbox" in message
    assert SETTING_KEY not in message
    assert STALE_LOOPBACK not in message


def test_stale_value_with_zero_enabled_endpoints_fails_closed(linkage_env) -> None:
    env = linkage_env
    env.contexts = {}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(STALE_LOOPBACK, SETTING_KEY)

    assert "No enabled netbox-proxbox FastAPI endpoint" in str(excinfo.value)
    assert SETTING_KEY not in str(excinfo.value)


def test_an_explicit_match_disambiguates_two_enabled_endpoints_without_a_warning(linkage_env) -> None:
    env = linkage_env
    env.contexts = {
        1: env.context(http_url=BACKEND_A, headers={API_KEY_HEADER: "key-a"}),
        2: env.context(http_url=BACKEND_B, headers={API_KEY_HEADER: "key-b"}),
    }

    result = env.linkage.resolve_proxbox_linkage(BACKEND_B, "")

    assert result.base_url == BACKEND_B
    assert result.api_key == "key-b"
    assert result.source == env.linkage.SOURCE_OVERRIDE
    assert result.warning == ""


def test_explicit_override_matching_two_endpoints_fails_closed(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A), 2: env.context(http_url=BACKEND_A)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError, match="more than one enabled"):
        env.linkage.resolve_proxbox_linkage(BACKEND_A, "")


MALFORMED_VALUES = (
    "not-a-url",
    _url("backend-a.example.invalid", 8000, "ftp"),
    "https://user:s3cretpw@backend-a.example.invalid:8000",
)


@pytest.mark.parametrize("configured", MALFORMED_VALUES)
def test_malformed_stored_value_is_stale_and_never_echoed(linkage_env, configured) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_B)}

    result = env.linkage.resolve_proxbox_linkage(configured, "")

    assert result.base_url == BACKEND_B
    assert result.warning == env.linkage.STALE_SETTING_WARNING
    assert "s3cretpw" not in result.warning


@pytest.mark.parametrize("configured", MALFORMED_VALUES)
def test_malformed_stored_value_with_two_endpoints_fails_closed(linkage_env, configured) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A), 2: env.context(http_url=BACKEND_B)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(configured, "")

    assert "s3cretpw" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Enabled-row cardinality: an enabled endpoint without a usable context still counts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("configured", ("", None, LEGACY_EXAMPLE, STALE_LOOPBACK))
def test_several_enabled_rows_fail_closed_when_one_context_is_unusable(linkage_env, configured) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A), 2: None}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(configured, SETTING_KEY)

    assert "More than one enabled netbox-proxbox FastAPI endpoint" in str(excinfo.value)
    assert SETTING_KEY not in str(excinfo.value)


@pytest.mark.parametrize("configured", ("", _url("backend-z.example.invalid")))
def test_a_single_enabled_row_without_a_usable_context_fails_closed(linkage_env, configured) -> None:
    env = linkage_env
    env.contexts = {1: None}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage(configured, SETTING_KEY)

    message = str(excinfo.value)
    assert "no usable request context" in message
    assert "target fingerprint" in message
    assert "netbox-proxbox" in message
    assert SETTING_KEY not in message


def test_an_explicit_match_still_disambiguates_when_another_enabled_row_is_unusable(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: None, 2: env.context(http_url=BACKEND_B)}

    result = env.linkage.resolve_proxbox_linkage(BACKEND_B, "")

    assert result.base_url == BACKEND_B
    assert result.source == env.linkage.SOURCE_OVERRIDE
    assert result.warning == ""


def test_an_explicit_match_cannot_select_an_unusable_row(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: None, 2: env.context(http_url=BACKEND_B)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError, match="More than one"):
        env.linkage.resolve_proxbox_linkage(BACKEND_A, "")


def test_the_context_slots_keep_one_entry_per_enabled_row(linkage_env) -> None:
    env = linkage_env
    usable = env.context(http_url=BACKEND_A)
    env.contexts = {1: usable, 2: None, 3: None}

    assert env.authorization.enabled_fastapi_context_slots() == [usable, None, None]
    assert env.authorization.enabled_fastapi_contexts() == [usable]


# ---------------------------------------------------------------------------
# Authentication: the packer key wins when set; otherwise the context key is used.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("configured_url", ("", BACKEND_A))
def test_key_comes_from_the_netbox_proxbox_context_when_the_setting_is_blank(linkage_env, configured_url) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}

    assert env.linkage.resolve_proxbox_linkage(configured_url, "").api_key == CONTEXT_KEY
    assert env.linkage.resolve_proxbox_linkage(configured_url, None).api_key == CONTEXT_KEY


@pytest.mark.parametrize("configured_url", ("", BACKEND_A))
def test_key_comes_from_the_setting_when_it_is_set(linkage_env, configured_url) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}

    assert env.linkage.resolve_proxbox_linkage(configured_url, SETTING_KEY).api_key == SETTING_KEY


def test_context_header_name_is_matched_case_insensitively(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(headers={"x-proxbox-api-key": CONTEXT_KEY})}

    assert env.linkage.resolve_proxbox_linkage("", "").api_key == CONTEXT_KEY


def test_a_context_without_a_key_yields_an_empty_key(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(headers={})}

    assert env.linkage.resolve_proxbox_linkage("", "").api_key == ""


def test_the_linkage_repr_never_exposes_the_key(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context()}

    result = env.linkage.resolve_proxbox_linkage("", SETTING_KEY)

    assert SETTING_KEY not in repr(result)
    assert CONTEXT_KEY not in repr(result)


def test_default_linkage_errors_never_contain_either_key(linkage_env) -> None:
    env = linkage_env
    env.contexts = {1: env.context(), 2: env.context(http_url=BACKEND_B)}

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError) as excinfo:
        env.linkage.resolve_proxbox_linkage("", SETTING_KEY)

    assert SETTING_KEY not in str(excinfo.value)
    assert CONTEXT_KEY not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Wiring: dispatch and the running build use the derived linkage.
# ---------------------------------------------------------------------------


def _import_jobs(monkeypatch):
    conf = types.ModuleType("django.conf")
    conf.settings = SimpleNamespace(PLUGINS_CONFIG={"netbox_packer": {}}, SECRET_KEY="test-secret")
    timezone_mod = types.ModuleType("django.utils.timezone")
    timezone_mod.now = Mock()
    django_utils = types.ModuleType("django.utils")
    django_utils.timezone = timezone_mod
    jobs_mod = types.ModuleType("netbox.jobs")
    jobs_mod.JobRunner = type("JobRunner", (), {})
    monkeypatch.setitem(sys.modules, "django.conf", conf)
    monkeypatch.setitem(sys.modules, "django.utils", django_utils)
    monkeypatch.setitem(sys.modules, "django.utils.timezone", timezone_mod)
    monkeypatch.setitem(sys.modules, "netbox", types.ModuleType("netbox"))
    monkeypatch.setitem(sys.modules, "netbox.jobs", jobs_mod)
    return importlib.import_module("netbox_packer.jobs")


def test_dispatch_authorizes_against_the_derived_base_url(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}
    jobs = _import_jobs(monkeypatch)
    settings_row = SimpleNamespace(proxbox_api_url="", get_proxbox_api_key=lambda: "")
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerPluginSettings = type("PackerPluginSettings", (), {"get_solo": staticmethod(lambda: settings_row)})
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models_mod)
    authorize = Mock()
    jobs._authorize_selected_endpoint = authorize
    jobs._endpoint_urls_for_cloud_dispatch = lambda _build: ["https://pve.example.invalid:8006"]
    build = SimpleNamespace(
        template=SimpleNamespace(installer_config=SimpleNamespace(installer_type="cloud_config")),
    )

    jobs._authorize_cloud_build_dispatch(build)

    authorize.assert_called_once_with("https://pve.example.invalid:8006", BACKEND_A)


def test_dispatch_fails_closed_when_no_linkage_can_be_resolved(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {}
    jobs = _import_jobs(monkeypatch)
    settings_row = SimpleNamespace(proxbox_api_url="", get_proxbox_api_key=lambda: "")
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerPluginSettings = type("PackerPluginSettings", (), {"get_solo": staticmethod(lambda: settings_row)})
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models_mod)
    authorize = Mock()
    jobs._authorize_selected_endpoint = authorize
    jobs._endpoint_urls_for_cloud_dispatch = lambda _build: ["https://pve.example.invalid:8006"]
    build = SimpleNamespace(
        template=SimpleNamespace(installer_config=SimpleNamespace(installer_type="cloud_config")),
    )

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError, match="netbox-proxbox"):
        jobs._authorize_cloud_build_dispatch(build)

    authorize.assert_not_called()


def test_running_build_logs_a_secret_free_error_when_the_linkage_fails(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {1: env.context(), 2: env.context(http_url=BACKEND_B)}
    jobs = _import_jobs(monkeypatch)
    settings_row = SimpleNamespace(proxbox_api_url="", get_proxbox_api_key=lambda: SETTING_KEY)
    build = SimpleNamespace(log="[INFO] started", save=Mock())

    with pytest.raises(env.authorization.PackerEndpointAuthorizationError):
        jobs._resolve_build_linkage(build, settings_row)

    assert "More than one enabled netbox-proxbox FastAPI endpoint" in build.log
    assert build.log.startswith("[INFO] started")
    assert SETTING_KEY not in build.log
    assert CONTEXT_KEY not in build.log
    build.save.assert_called_once_with(update_fields=["log"])


def test_running_build_resolves_the_linkage_from_the_settings_row(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}
    jobs = _import_jobs(monkeypatch)
    settings_row = SimpleNamespace(proxbox_api_url="", get_proxbox_api_key=lambda: "")
    build = SimpleNamespace(log="", save=Mock())

    result = jobs._resolve_build_linkage(build, settings_row)

    assert (result.base_url, result.api_key, result.source) == (
        BACKEND_A,
        CONTEXT_KEY,
        env.linkage.SOURCE_NETBOX_PROXBOX,
    )
    build.save.assert_not_called()


def test_running_build_logs_the_stale_setting_warning_without_secrets(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}
    jobs = _import_jobs(monkeypatch)
    settings_row = SimpleNamespace(proxbox_api_url=STALE_LOOPBACK, get_proxbox_api_key=lambda: SETTING_KEY)
    build = SimpleNamespace(log="", save=Mock())

    linkage = jobs._resolve_build_linkage(build, settings_row)
    lines = jobs._linkage_log_lines(linkage)

    assert lines[0] == f"[INFO] Delegating real Proxmox bake to proxbox-api: {BACKEND_A} (linkage: {linkage.source})"
    assert lines[1] == f"[WARNING] {env.linkage.STALE_SETTING_WARNING}"
    joined = "\n".join(lines)
    for secret in (SETTING_KEY, CONTEXT_KEY, STALE_LOOPBACK):
        assert secret not in joined


def test_log_lines_have_no_warning_for_the_default_linkage(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}
    jobs = _import_jobs(monkeypatch)

    lines = jobs._linkage_log_lines(env.linkage.resolve_proxbox_linkage("", ""))

    assert len(lines) == 1
    assert "[WARNING]" not in lines[0]


def test_dispatch_authorizes_against_the_endpoint_not_the_stale_value(linkage_env, monkeypatch) -> None:
    env = linkage_env
    env.contexts = {1: env.context(http_url=BACKEND_A)}
    jobs = _import_jobs(monkeypatch)
    settings_row = SimpleNamespace(proxbox_api_url=STALE_LOOPBACK, get_proxbox_api_key=lambda: "")
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerPluginSettings = type("PackerPluginSettings", (), {"get_solo": staticmethod(lambda: settings_row)})
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models_mod)
    authorize = Mock()
    jobs._authorize_selected_endpoint = authorize
    jobs._endpoint_urls_for_cloud_dispatch = lambda _build: ["https://pve.example.invalid:8006"]
    build = SimpleNamespace(
        template=SimpleNamespace(installer_config=SimpleNamespace(installer_type="cloud_config")),
    )

    jobs._authorize_cloud_build_dispatch(build)

    authorize.assert_called_once_with("https://pve.example.invalid:8006", BACKEND_A)
