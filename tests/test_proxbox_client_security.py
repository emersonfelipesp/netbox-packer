"""Security contract tests for the credential-bearing proxbox-api client."""

from __future__ import annotations

import importlib.util
import io
import json
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_proxbox_client() -> ModuleType:
    path = ROOT / "netbox_packer" / "proxbox_client.py"
    spec = importlib.util.spec_from_file_location("netbox_packer_proxbox_client_security", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load proxbox client from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _running_server(handler: type[BaseHTTPRequestHandler]) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("configured", "expected"),
    (
        ("https://proxbox-api.example/", "https://proxbox-api.example"),
        ("https://127.0.0.1:8443", "https://127.0.0.1:8443"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
        ("http://[::1]:8000/", "http://[::1]:8000"),
    ),
)
def test_normalize_proxbox_api_base_url_accepts_one_exact_origin(configured: str, expected: str) -> None:
    client = _load_proxbox_client()

    assert client.normalize_proxbox_api_base_url(configured) == expected


@pytest.mark.parametrize(
    "configured",
    (
        "",
        " proxbox-api.example",
        "https://proxbox-api.example ",
        "proxbox-api.example",
        "ftp://proxbox-api.example",
        "file:///tmp/proxbox.sock",
        "http://proxbox-api.example",
        "http://10.0.30.207:8000",
        "http://localhost:8000",
        "http:///missing-host",
        "https://user@proxbox-api.example",
        "https://user:password@proxbox-api.example",
        "https://proxbox-api.example/api",
        "https://proxbox-api.example/?next=other",
        "https://proxbox-api.example?",
        "https://proxbox-api.example/#fragment",
        "https://proxbox-api.example#",
        "https://proxbox-api.example:",
        "https://proxbox-api.example:0",
        "https://proxbox-api.example:70000",
        "https://proxbox-api.example\\@other.example",
        "https://proxbox-api%2eexample",
        "https://próxbox-api.example",
        "https://proxbox-api.example\n.other.example",
    ),
)
def test_normalize_proxbox_api_base_url_rejects_ambiguous_or_unsafe_values(configured: str) -> None:
    client = _load_proxbox_client()

    with pytest.raises(client.ProxboxApiError, match="proxbox-api base URL"):
        client.normalize_proxbox_api_base_url(configured)


def test_invalid_base_url_fails_before_any_network_call(monkeypatch) -> None:
    client = _load_proxbox_client()
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(client, "_open_request", fail_if_called)

    with pytest.raises(client.ProxboxApiError, match="must not include a path"):
        client._post_json(
            proxbox_api_url="https://proxbox-api.example/untrusted",
            proxbox_api_key="canary-not-a-secret",
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=201,
        )

    assert called is False


def test_malformed_authority_is_absent_from_complete_traceback() -> None:
    client = _load_proxbox_client()
    canary = "legacy-authority-canary-not-a-secret"
    malformed = f"https://legacy-user:{canary}@proxbox-api.example／redirect"

    try:
        client.normalize_proxbox_api_base_url(malformed)
    except client.ProxboxApiError as exc:
        rendered = "".join(traceback.format_exception(exc))
        assert canary not in rendered
        assert malformed not in rendered
        assert exc.__suppress_context__ is True
    else:  # pragma: no cover - the malformed NFKC authority must be rejected
        raise AssertionError("expected ProxboxApiError")


def test_vm_provision_uses_the_same_strict_origin_guard(monkeypatch) -> None:
    client = _load_proxbox_client()
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(client, "_open_request", fail_if_called)

    with pytest.raises(client.ProxboxApiError, match="must not include a query string"):
        client.call_proxbox_vm_provision(
            proxbox_api_url="https://proxbox-api.example?redirect=other",
            proxbox_api_key="canary-not-a-secret",
            endpoint_id=1,
            template_vmid=9000,
            new_vmid=9001,
            new_name="test-vm",
            target_node="pve01",
            cloud_init={},
        )

    assert called is False


class _Response:
    def __init__(self, body: bytes, *, content_length: str | None = None, status: int = 200):
        self.body = body
        self.content_length = content_length
        self.offset = 0
        self.status = status

    def getheader(self, name: str) -> str | None:
        return self.content_length if name == "Content-Length" else None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


_RECIPE_DIGEST = "a" * 64
_PLAN_TOKEN = "p" * 64
_PLAN_ID = "00000000-0000-0000-0000-000000000001"


def _install_build_sequence(
    monkeypatch,
    client,
    *,
    endpoint_id: int = 1,
    target_node: str = "pve01",
    vmid: int = 9000,
    preflight: dict | None = None,
    executed: dict | None = None,
) -> list[dict[str, object]]:
    plan = {
        "contract_version": "2.0",
        "status": "planned",
        "endpoint_id": endpoint_id,
        "target_node": target_node,
        "vmid": vmid,
        "template_vmid": vmid,
        "recipe_digest": _RECIPE_DIGEST,
    }
    expected_operation_id = str(executed.get("operation_id")) if executed is not None else _PLAN_ID
    preflight = preflight or {
        "contract_version": "1.0",
        "endpoint_id": endpoint_id,
        "target_node": target_node,
        "vmid": vmid,
        "ready": True,
        "writes_enabled": True,
        "recipe_digest": _RECIPE_DIGEST,
        "plan_id": expected_operation_id,
        "plan_token": _PLAN_TOKEN,
    }
    executed = executed or {
        "contract_version": "2.0",
        "status": "completed",
        "endpoint_id": endpoint_id,
        "target_node": target_node,
        "vmid": vmid,
        "template_vmid": vmid,
        "returncode": 0,
        "operation_id": _PLAN_ID,
        "recipe_digest": _RECIPE_DIGEST,
        "verified": True,
        "recovery_required": False,
    }
    responses = iter(
        (
            _Response(json.dumps(plan).encode(), status=201),
            _Response(json.dumps(preflight).encode(), status=200),
            _Response(json.dumps(executed).encode(), status=201),
        )
    )
    requests: list[dict[str, object]] = []

    def fake_open_request(request, *, timeout: int):
        requests.append(
            {
                "url": request.full_url,
                "headers": {key.lower(): value for key, value in request.header_items()},
                "body": json.loads(request.data.decode()),
                "timeout": timeout,
            }
        )
        return next(responses)

    monkeypatch.setattr(client, "_open_request", fake_open_request)
    return requests


def test_transport_and_invalid_json_errors_remain_actionable_and_key_safe(monkeypatch) -> None:
    client = _load_proxbox_client()
    canary = "canary-not-a-secret"

    def unavailable(*args, **kwargs):
        raise client.urllib.error.URLError(f"reflected {canary}")

    monkeypatch.setattr(client, "_open_request", unavailable)
    with pytest.raises(client.ProxboxApiError, match="before a valid response") as transport_error:
        client._post_json(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key=canary,
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=201,
        )
    assert canary not in str(transport_error.value)
    assert transport_error.value.__suppress_context__ is True

    def non_json_response(_request, *, timeout: int) -> _Response:
        return _Response(f"not JSON; reflected {canary}".encode())

    monkeypatch.setattr(client, "_open_request", non_json_response)
    with pytest.raises(client.ProxboxApiError, match="invalid JSON") as response_error:
        client._post_json(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key=canary,
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=200,
        )
    assert canary not in str(response_error.value)
    assert response_error.value.__suppress_context__ is True


def test_http_error_body_is_never_copied_into_exception(monkeypatch) -> None:
    client = _load_proxbox_client()
    canary = "canary-not-a-secret"

    def rejected(request, *, timeout: int):
        raise client.urllib.error.HTTPError(
            request.full_url,
            500,
            "backend failure",
            {},
            io.BytesIO(f"reflected {canary}".encode()),
        )

    monkeypatch.setattr(client, "_open_request", rejected)
    with pytest.raises(client.ProxboxApiError, match="HTTP 500") as exc_info:
        client._post_json(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key=canary,
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=200,
        )

    assert canary not in str(exc_info.value)
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.parametrize("api_key", ("", " leading", "trailing ", "line\r\nbreak", "café", "x" * 4097))
@pytest.mark.parametrize("caller", ("build", "provision"))
def test_public_callers_reject_unsafe_api_keys_before_network(monkeypatch, api_key: str, caller: str) -> None:
    client = _load_proxbox_client()
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(client, "_open_request", fail_if_called)
    with pytest.raises(client.ProxboxApiError, match="header-safe") as exc_info:
        if caller == "build":
            client.call_proxbox_build(
                proxbox_api_url="https://proxbox-api.example",
                proxbox_api_key=api_key,
                name="test",
                vmid=9000,
                endpoint_id=1,
                target_node="pve01",
                image_url="https://images.example/test.qcow2",
                user_data_yaml="#cloud-config\n",
            )
        else:
            client.call_proxbox_vm_provision(
                proxbox_api_url="https://proxbox-api.example",
                proxbox_api_key=api_key,
                endpoint_id=1,
                template_vmid=9000,
                new_vmid=9001,
                new_name="test-vm",
                target_node="pve01",
                cloud_init={},
            )

    if api_key:
        assert api_key not in str(exc_info.value)
    assert called is False


@pytest.mark.parametrize("body", (b"null", b"[]", b'"scalar"'))
def test_non_object_json_responses_are_rejected(monkeypatch, body: bytes) -> None:
    client = _load_proxbox_client()
    monkeypatch.setattr(client, "_open_request", lambda *_args, **_kwargs: _Response(body))

    with pytest.raises(client.ProxboxApiError, match="JSON object"):
        client._post_json(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=200,
        )


def test_invalid_utf8_response_is_rejected_without_echoing_body(monkeypatch) -> None:
    client = _load_proxbox_client()
    monkeypatch.setattr(client, "_open_request", lambda *_args, **_kwargs: _Response(b'"\xff"'))

    with pytest.raises(client.ProxboxApiError, match="invalid JSON") as exc_info:
        client._post_json(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=200,
        )

    assert "\\xff" not in str(exc_info.value)


@pytest.mark.parametrize(
    "response",
    (
        _Response(b"", content_length=str(1024 * 1024 + 1)),
        _Response(b"{}", content_length="not-a-number"),
        _Response(b"x" * (1024 * 1024 + 1)),
    ),
)
def test_oversized_or_invalidly_framed_responses_are_rejected(monkeypatch, response: _Response) -> None:
    client = _load_proxbox_client()
    monkeypatch.setattr(client, "_open_request", lambda *_args, **_kwargs: response)

    with pytest.raises(client.ProxboxApiError, match="size|framing"):
        client._post_json(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            path="/cloud/templates/images",
            payload={},
            timeout=1,
            expected_status=200,
        )


def test_response_body_has_an_overall_read_deadline(monkeypatch) -> None:
    client = _load_proxbox_client()
    response = _Response(b'{"status":"completed"}')
    clock = iter((100.0, 100.0, 131.0))
    monkeypatch.setattr(client.time, "monotonic", lambda: next(clock))

    with pytest.raises(client.ProxboxApiError, match="timed out"):
        client._read_bounded_response(response, request_timeout=3900)


@pytest.mark.parametrize(
    ("caller", "unexpected_status"),
    (("build", 200), ("build", 202), ("provision", 201), ("provision", 202)),
)
def test_public_callers_reject_unexpected_success_status_without_reading_body(
    monkeypatch,
    caller: str,
    unexpected_status: int,
) -> None:
    client = _load_proxbox_client()
    response = _Response(
        b'{"status":"completed","vmid":9000,"new_vmid":9001}',
        status=unexpected_status,
    )
    monkeypatch.setattr(client, "_open_request", lambda *_args, **_kwargs: response)

    with pytest.raises(client.ProxboxApiError, match="unexpected HTTP success status"):
        if caller == "build":
            client.call_proxbox_build(
                proxbox_api_url="https://proxbox-api.example",
                proxbox_api_key="canary-not-a-secret",
                name="test",
                vmid=9000,
                endpoint_id=1,
                target_node="pve01",
                image_url="https://images.example/test.qcow2",
                user_data_yaml="#cloud-config\n",
            )
        else:
            client.call_proxbox_vm_provision(
                proxbox_api_url="https://proxbox-api.example",
                proxbox_api_key="canary-not-a-secret",
                endpoint_id=1,
                template_vmid=9000,
                new_vmid=9001,
                new_name="test-vm",
                target_node="pve01",
                cloud_init={},
            )

    assert response.offset == 0


def test_credentialed_opener_disables_environment_proxies(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8443")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:1080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    client = _load_proxbox_client()
    proxy_handlers = [
        handler
        for handler in client._NO_REDIRECT_OPENER.handlers
        if isinstance(handler, client.urllib.request.ProxyHandler)
    ]

    # Passing ProxyHandler({}) suppresses the default environment-aware handler.
    # urllib omits the empty explicit handler from the final handler list because
    # it has no proxy protocol methods to register.
    assert proxy_handlers == []
    source = (ROOT / "netbox_packer" / "proxbox_client.py").read_text(encoding="utf-8")
    assert "urllib.request.ProxyHandler({})" in source


@pytest.mark.parametrize(
    "update",
    (
        {"contract_version": "1.0"},
        {"status": "submitted"},
        {"status": "COMPLETED"},
        {"endpoint_id": 2},
        {"target_node": "pve02"},
        {"vmid": True},
        {"vmid": 99},
        {"vmid": 9001},
        {"template_vmid": 9001},
        {"returncode": 1},
        {"status": "failed", "verified": False, "returncode": 0},
        {"status": "planned"},
        {"operation_id": ""},
        {"operation_id": "00000000-0000-0000-0000-000000000002"},
        {"recipe_digest": None},
        {"recipe_digest": "b" * 64},
        {"verified": False},
        {"recovery_required": True},
    ),
)
def test_template_build_response_rejects_invalid_status_binding_and_result(update: dict) -> None:
    client = _load_proxbox_client()
    response = {
        "contract_version": "2.0",
        "status": "completed",
        "endpoint_id": 1,
        "target_node": "pve01",
        "vmid": 9000,
        "template_vmid": 9000,
        "returncode": 0,
        "operation_id": _PLAN_ID,
        "recipe_digest": _RECIPE_DIGEST,
        "verified": True,
        "recovery_required": False,
        **update,
    }

    with pytest.raises(client.ProxboxApiError, match="template-build response"):
        client._validate_build_response(
            response,
            expected_endpoint_id=1,
            expected_target_node="pve01",
            expected_vmid=9000,
            expected_recipe_digest=_RECIPE_DIGEST,
            expected_operation_id=_PLAN_ID,
        )


def test_template_build_response_discards_secret_bearing_backend_fields(monkeypatch) -> None:
    client = _load_proxbox_client()
    canary = "backend-canary-not-a-secret"
    response = {
        "contract_version": "2.0",
        "status": "completed",
        "endpoint_id": 1,
        "target_node": "pve01",
        "vmid": 9000,
        "template_vmid": 9000,
        "returncode": 0,
        "operation_id": _PLAN_ID,
        "recipe_digest": _RECIPE_DIGEST,
        "verified": True,
        "recovery_required": False,
        "build_script": f"export TOKEN={canary}",
        "stdout": f"token={canary}",
        "stderr": f"failed with {canary}",
        "generated_userdata": f"password: {canary}",
        "commands": [f"echo {canary}"],
    }
    _install_build_sequence(monkeypatch, client, executed=response)

    result = client.call_proxbox_build(
        proxbox_api_url="https://proxbox-api.example",
        proxbox_api_key="canary-not-a-secret",
        name="test",
        vmid=9000,
        endpoint_id=1,
        target_node="pve01",
        image_url="https://images.example/test.qcow2",
        user_data_yaml="#cloud-config\n",
    )

    assert result == {
        "status": "completed",
        "vmid": 9000,
        "template_vmid": 9000,
        "returncode": 0,
        "operation_id": "00000000-0000-0000-0000-000000000001",
        "verified": True,
        "recovery_required": False,
    }
    assert canary not in repr(result)


def test_template_build_response_accepts_minimum_supported_api_plan_contract(monkeypatch) -> None:
    client = _load_proxbox_client()
    plan = {
        "contract_version": "2.0",
        "status": "planned",
        "endpoint_id": 1,
        "target_node": "pve01",
        "vmid": 9000,
        "template_vmid": 9000,
        "recipe_digest": _RECIPE_DIGEST,
    }
    monkeypatch.setattr(
        client,
        "_open_request",
        lambda *_args, **_kwargs: _Response(json.dumps(plan).encode(), status=201),
    )

    result = client.call_proxbox_build(
        proxbox_api_url="https://proxbox-api.example",
        proxbox_api_key="canary-not-a-secret",
        name="test",
        vmid=9000,
        endpoint_id=1,
        target_node="pve01",
        image_url="https://images.example/test.qcow2",
        user_data_yaml="#cloud-config\n",
        execute=False,
    )

    assert result == {"status": "planned", "vmid": 9000, "template_vmid": 9000}


@pytest.mark.parametrize(
    ("endpoint_id", "target_node"),
    ((None, "pve01"), (0, "pve01"), (True, "pve01"), (1, ""), (1, " pve01")),
)
def test_executable_build_rejects_unbound_target_before_network(
    monkeypatch,
    endpoint_id: object,
    target_node: str,
) -> None:
    client = _load_proxbox_client()
    network = pytest.fail
    monkeypatch.setattr(client, "_open_request", network)

    with pytest.raises(client.ProxboxApiError, match="positive endpoint_id and nonempty target_node"):
        client.call_proxbox_build(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            name="test",
            vmid=9000,
            endpoint_id=endpoint_id,
            target_node=target_node,
            image_url="https://images.example/test.qcow2",
            user_data_yaml="#cloud-config\n",
        )


def test_build_preflight_denial_never_attempts_execution(monkeypatch) -> None:
    client = _load_proxbox_client()
    requests = _install_build_sequence(
        monkeypatch,
        client,
        preflight={
            "contract_version": "1.0",
            "endpoint_id": 1,
            "target_node": "pve01",
            "vmid": 9000,
            "ready": False,
            "writes_enabled": False,
            "recipe_digest": _RECIPE_DIGEST,
        },
    )

    with pytest.raises(client.ProxboxApiError, match="preflight denied"):
        client.call_proxbox_build(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            name="test",
            vmid=9000,
            endpoint_id=1,
            target_node="pve01",
            image_url="https://images.example/test.qcow2",
            user_data_yaml="#cloud-config\n",
        )

    assert len(requests) == 2
    assert requests[-1]["url"] == "https://proxbox-api.example/cloud/templates/images/preflight"


def test_recovery_required_response_is_preserved_without_backend_output(monkeypatch) -> None:
    client = _load_proxbox_client()
    canary = "recovery-output-canary-not-a-secret"
    _install_build_sequence(
        monkeypatch,
        client,
        executed={
            "contract_version": "2.0",
            "status": "recovery_required",
            "endpoint_id": 1,
            "target_node": "pve01",
            "vmid": 9000,
            "template_vmid": 9000,
            "returncode": 0,
            "operation_id": "00000000-0000-0000-0000-000000000002",
            "recipe_digest": _RECIPE_DIGEST,
            "verified": False,
            "recovery_required": True,
            "stdout": canary,
            "diagnostics": [{"message": canary}],
        },
    )

    result = client.call_proxbox_build(
        proxbox_api_url="https://proxbox-api.example",
        proxbox_api_key="canary-not-a-secret",
        name="test",
        vmid=9000,
        endpoint_id=1,
        target_node="pve01",
        image_url="https://images.example/test.qcow2",
        user_data_yaml="#cloud-config\n",
    )

    assert result == {
        "status": "recovery_required",
        "vmid": 9000,
        "template_vmid": 9000,
        "returncode": 0,
        "operation_id": "00000000-0000-0000-0000-000000000002",
        "verified": False,
        "recovery_required": True,
    }
    assert canary not in repr(result)


def test_execution_response_loss_recovers_by_persisted_plan_id(monkeypatch) -> None:
    client = _load_proxbox_client()
    events: list[str] = []
    responses = iter(
        (
            _Response(
                json.dumps(
                    {
                        "contract_version": "2.0",
                        "status": "planned",
                        "endpoint_id": 1,
                        "target_node": "pve01",
                        "vmid": 9000,
                        "template_vmid": 9000,
                        "recipe_digest": _RECIPE_DIGEST,
                    }
                ).encode(),
                status=201,
            ),
            _Response(
                json.dumps(
                    {
                        "contract_version": "1.0",
                        "endpoint_id": 1,
                        "target_node": "pve01",
                        "vmid": 9000,
                        "ready": True,
                        "writes_enabled": True,
                        "recipe_digest": _RECIPE_DIGEST,
                        "plan_id": _PLAN_ID,
                        "plan_token": _PLAN_TOKEN,
                    }
                ).encode(),
                status=200,
            ),
        )
    )

    def fake_open(request, *, timeout: int):
        del timeout
        if request.method == "GET":
            events.append("operation-get")
            return _Response(
                json.dumps(
                    {
                        "operation_id": _PLAN_ID,
                        "endpoint_id": 1,
                        "target_node": "pve01",
                        "vmid": 9000,
                        "provider": "release_image",
                        "state": "completed",
                        "recipe_digest": _RECIPE_DIGEST,
                        "plan_digest": "b" * 64,
                        "verified": True,
                        "recovery_required": False,
                        "cancel_requested": False,
                        "created_at": 1.0,
                        "updated_at": 2.0,
                    }
                ).encode(),
                status=200,
            )
        if request.data and json.loads(request.data.decode()).get("execute") is True:
            events.append("execute-accepted-response-lost")
            raise client.urllib.error.URLError("response lost after acceptance")
        events.append("plan-or-preflight")
        return next(responses)

    def persist(operation_id: str) -> None:
        assert operation_id == _PLAN_ID
        events.append("persist-operation")

    monkeypatch.setattr(client, "_open_request", fake_open)
    result = client.call_proxbox_build(
        proxbox_api_url="https://proxbox-api.example",
        proxbox_api_key="canary-not-a-secret",
        name="test",
        vmid=9000,
        endpoint_id=1,
        target_node="pve01",
        image_url="https://images.example/test.qcow2",
        user_data_yaml="#cloud-config\n",
        operation_planned=persist,
    )

    assert result["status"] == "completed"
    assert result["operation_id"] == _PLAN_ID
    assert events == [
        "plan-or-preflight",
        "plan-or-preflight",
        "persist-operation",
        "execute-accepted-response-lost",
        "operation-get",
    ]


def test_ambiguous_running_operation_requires_explicit_recovery(monkeypatch) -> None:
    client = _load_proxbox_client()
    operation = {
        "operation_id": _PLAN_ID,
        "endpoint_id": 1,
        "target_node": "pve01",
        "vmid": 9000,
        "provider": "release_image",
        "state": "running",
        "recipe_digest": _RECIPE_DIGEST,
        "verified": False,
        "recovery_required": False,
    }
    monkeypatch.setattr(client, "_get_json", lambda **_kwargs: operation)

    with pytest.raises(client.ProxboxOperationRecoveryRequired) as exc_info:
        client._recover_ambiguous_build_response(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            timeout=10,
            endpoint_id=1,
            target_node="pve01",
            vmid=9000,
            recipe_digest=_RECIPE_DIGEST,
            operation_id=_PLAN_ID,
        )

    assert exc_info.value.operation_id == _PLAN_ID


@pytest.mark.parametrize(
    ("verified", "recovery_required"),
    ((True, False), (False, True)),
)
def test_cancelled_operation_rejects_inconsistent_success_or_recovery_flags(
    verified: bool, recovery_required: bool
) -> None:
    client = _load_proxbox_client()
    operation = {
        "operation_id": _PLAN_ID,
        "endpoint_id": 1,
        "target_node": "pve01",
        "vmid": 9000,
        "provider": "release_image",
        "recipe_digest": _RECIPE_DIGEST,
        "state": "cancelled",
        "verified": verified,
        "recovery_required": recovery_required,
    }
    with pytest.raises(client.ProxboxApiError, match="inconsistent"):
        client._validate_build_operation_response(
            operation,
            expected_endpoint_id=1,
            expected_target_node="pve01",
            expected_vmid=9000,
            expected_recipe_digest=_RECIPE_DIGEST,
            expected_operation_id=_PLAN_ID,
        )


@pytest.mark.parametrize("storage", ("a" * 65, "local:images", "-local", "local/images"))
def test_storage_identifier_rejects_values_outside_v021_contract(storage: str) -> None:
    client = _load_proxbox_client()

    with pytest.raises(ValueError, match="storage"):
        client.validate_proxbox_storage_identifier(storage)


def test_storage_identifier_accepts_exact_64_character_boundary() -> None:
    client = _load_proxbox_client()
    storage = "a" + "b" * 63

    assert client.validate_proxbox_storage_identifier(storage) == storage


@pytest.mark.parametrize(
    ("response", "start_after_provision"),
    (
        ({}, True),
        ({"status": "submitted", "new_vmid": 9001}, True),
        ({"status": "started", "new_vmid": True}, True),
        ({"status": "started", "new_vmid": 99}, True),
        ({"status": "started", "new_vmid": 9002}, True),
        ({"status": "stopped", "new_vmid": 9001}, True),
        ({"status": "started", "new_vmid": 9001}, False),
    ),
)
def test_vm_provision_response_rejects_invalid_status_or_vmid(
    monkeypatch,
    response: dict,
    start_after_provision: bool,
) -> None:
    client = _load_proxbox_client()
    monkeypatch.setattr(
        client,
        "_open_request",
        lambda *_args, **_kwargs: _Response(json.dumps(response).encode()),
    )

    with pytest.raises(client.ProxboxApiError, match="VM-provision response"):
        client.call_proxbox_vm_provision(
            proxbox_api_url="https://proxbox-api.example",
            proxbox_api_key="canary-not-a-secret",
            endpoint_id=1,
            template_vmid=9000,
            new_vmid=9001,
            new_name="test-vm",
            target_node="pve01",
            cloud_init={},
            start_after_provision=start_after_provision,
        )


def test_vm_provision_response_returns_only_validated_metadata(monkeypatch) -> None:
    client = _load_proxbox_client()
    canary = "detail-canary-not-a-secret"
    monkeypatch.setattr(
        client,
        "_open_request",
        lambda *_args, **_kwargs: _Response(
            json.dumps({"status": "started", "new_vmid": 9001, "detail": canary}).encode()
        ),
    )

    result = client.call_proxbox_vm_provision(
        proxbox_api_url="https://proxbox-api.example",
        proxbox_api_key="canary-not-a-secret",
        endpoint_id=1,
        template_vmid=9000,
        new_vmid=9001,
        new_name="test-vm",
        target_node="pve01",
        cloud_init={},
    )

    assert result == {"status": "started", "new_vmid": 9001}
    assert canary not in repr(result)


def test_cross_origin_redirect_never_receives_api_key() -> None:
    client = _load_proxbox_client()
    observed_destination_keys: list[str | None] = []

    class DestinationHandler(BaseHTTPRequestHandler):
        def _record_request(self) -> None:
            observed_destination_keys.append(self.headers.get("X-Proxbox-API-Key"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"completed"}')

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            self._record_request()

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            self._record_request()

        def log_message(self, format: str, *args) -> None:
            return

    with _running_server(DestinationHandler) as destination:
        destination_url = f"http://127.0.0.1:{destination.server_port}/capture"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                self.send_response(302)
                self.send_header("Location", destination_url)
                self.end_headers()

            def log_message(self, format: str, *args) -> None:
                return

        with (
            _running_server(RedirectHandler) as redirector,
            pytest.raises(client.ProxboxApiError, match="refused HTTP redirect") as exc_info,
        ):
            client._post_json(
                proxbox_api_url=f"http://127.0.0.1:{redirector.server_port}",
                proxbox_api_key="canary-not-a-secret",
                path="/cloud/templates/images",
                payload={"name": "test"},
                timeout=2,
                expected_status=201,
            )

    assert observed_destination_keys == []
    assert destination_url not in str(exc_info.value)
    assert "canary-not-a-secret" not in str(exc_info.value)


def test_security_contract_is_documented_for_operators_and_agents() -> None:
    required = (
        "one exact HTTP(S) origin",
        "HTTPS is required",
        "literal loopback",
        "does not follow HTTP redirects",
        "does not use environment-configured HTTP proxies",
        "never include backend response bodies",
        "1 MiB",
        "discarded before durable logging",
        "userinfo",
        "query strings",
        "fragments",
    )
    for relative_path in ("README.md", "docs/configuration.md", "CLAUDE.md"):
        content = " ".join((ROOT / relative_path).read_text(encoding="utf-8").split())
        for text in required:
            assert text in content, f"{relative_path} must document {text!r}"


def test_all_credentialed_callers_preserve_raw_configuration_for_shared_validation() -> None:
    jobs = (ROOT / "netbox_packer" / "jobs.py").read_text(encoding="utf-8")
    views = (ROOT / "netbox_packer" / "views.py").read_text(encoding="utf-8")

    assert "normalize_proxbox_api_base_url(configured_api_url)" in jobs
    assert 'proxbox_api_url = settings_row.proxbox_api_url or ""' in views
    assert '(settings_row.proxbox_api_url or "").strip()' not in jobs
    assert '(settings_row.proxbox_api_url or "").strip()' not in views
    assert 'for key in ("build_script", "stdout", "stderr")' not in jobs
    assert "Base image source resolved (value withheld from durable logs)" in jobs
