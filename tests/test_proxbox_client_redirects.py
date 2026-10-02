"""Authenticated proxbox-api requests must never follow a redirect.

urllib replays request headers on a redirect, which would send the API key to
another origin (or over plain http after an https -> http downgrade). Every
authenticated helper therefore goes through an opener that refuses all redirects.
"""

from __future__ import annotations

import importlib.util
import ssl
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "netbox_packer" / "proxbox_client.py"
SPEC = importlib.util.spec_from_file_location("netbox_packer_proxbox_client_redirect_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)

API_KEY = "redirect-test-key-value"
REDIRECT_CODES = (301, 302, 303, 307, 308)


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []


def _serve(recorder: _Recorder, location_for) -> tuple[ThreadingHTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            recorder.requests.append((self.path, self.headers.get("X-Proxbox-API-Key", "")))
            location = location_for(self.path)
            if location is None:
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            code, target = location
            self.send_response(code)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_GET = _handle  # noqa: N815

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return server, server.server_address[1]


@pytest.fixture
def backends(monkeypatch):
    """Start an origin that redirects and a second origin that records any request."""

    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    first, second = _Recorder(), _Recorder()
    state = {"redirect": (302, "")}
    second_server, second_port = _serve(second, lambda _path: None)
    first_server, first_port = _serve(first, lambda _path: state["redirect"])
    env = type(
        "Env",
        (),
        {
            "first": first,
            "second": second,
            "first_url": f"http://127.0.0.1:{first_port}",
            "second_url": f"http://127.0.0.1:{second_port}",
            "state": state,
        },
    )
    yield env
    for server in (first_server, second_server):
        server.shutdown()
        server.server_close()


def _post(env, verify_ssl: bool):
    return client._post_json(
        proxbox_api_url=env.first_url,
        proxbox_api_key=API_KEY,
        path="/cloud/templates/images",
        payload={"name": "n"},
        timeout=5,
        verify_ssl=verify_ssl,
    )


def _assert_secret_free(error: Exception, env) -> None:
    message = str(error)
    assert API_KEY not in message
    assert "127.0.0.1" not in message
    assert env.second_url not in message
    assert "redirect" in message
    assert error.status_code in REDIRECT_CODES


@pytest.mark.parametrize("verify_ssl", (True, False))
@pytest.mark.parametrize("code", REDIRECT_CODES)
def test_cross_origin_redirect_is_refused_and_the_key_is_never_replayed(backends, code, verify_ssl) -> None:
    env = backends
    env.state["redirect"] = (code, env.second_url + "/stolen")

    with pytest.raises(client.ProxboxApiError) as excinfo:
        _post(env, verify_ssl)

    _assert_secret_free(excinfo.value, env)
    assert env.first.requests == [("/cloud/templates/images", API_KEY)]
    assert env.second.requests == []


@pytest.mark.parametrize("verify_ssl", (True, False))
@pytest.mark.parametrize("code", (301, 303))
def test_same_origin_redirect_is_refused_without_a_second_request(backends, code, verify_ssl) -> None:
    env = backends
    env.state["redirect"] = (code, env.first_url + "/elsewhere")

    with pytest.raises(client.ProxboxApiError) as excinfo:
        _post(env, verify_ssl)

    _assert_secret_free(excinfo.value, env)
    assert env.first.requests == [("/cloud/templates/images", API_KEY)]
    assert env.second.requests == []


@pytest.mark.parametrize("verify_ssl", (True, False))
def test_a_scheme_downgrade_redirect_is_refused_by_the_handler(verify_ssl) -> None:
    opener = client._build_opener(verify_ssl)
    handler = next(item for item in opener.handlers if isinstance(item, client._RefuseRedirects))
    request = urllib.request.Request("https://backend.example.invalid/cloud/templates/images", method="POST")
    request.add_header("X-Proxbox-API-Key", API_KEY)

    for code in (*REDIRECT_CODES, 300):
        with pytest.raises(client.ProxboxApiError) as excinfo:
            handler.redirect_request(request, None, code, "Found", {}, "http://insecure.example.invalid/")
        assert API_KEY not in str(excinfo.value)
        assert "insecure.example.invalid" not in str(excinfo.value)
        assert "backend.example.invalid" not in str(excinfo.value)


@pytest.mark.parametrize("verify_ssl", (True, False))
def test_the_opener_replaces_urllibs_default_redirect_handler(verify_ssl) -> None:
    opener = client._build_opener(verify_ssl)
    redirect_handlers = [item for item in opener.handlers if isinstance(item, urllib.request.HTTPRedirectHandler)]

    assert len(redirect_handlers) == 1
    assert isinstance(redirect_handlers[0], client._RefuseRedirects)


def test_tls_verification_is_only_disabled_when_the_endpoint_opted_out() -> None:
    def https_handlers(verify_ssl: bool):
        return [
            item for item in client._build_opener(verify_ssl).handlers if isinstance(item, urllib.request.HTTPSHandler)
        ]

    assert client._tls_handlers(True) == []
    (default_handler,) = https_handlers(True)
    assert default_handler._context.verify_mode == ssl.CERT_REQUIRED
    assert default_handler._context.check_hostname is True

    assert len(client._tls_handlers(False)) == 1
    (relaxed_handler,) = https_handlers(False)
    assert relaxed_handler._context.verify_mode == ssl.CERT_NONE
    assert relaxed_handler._context.check_hostname is False


@pytest.mark.parametrize("verify_ssl", (True, False))
def test_every_authenticated_helper_refuses_redirects(backends, verify_ssl) -> None:
    env = backends
    env.state["redirect"] = (302, env.second_url + "/stolen")

    with pytest.raises(client.ProxboxApiError) as build_error:
        client.call_proxbox_build(
            proxbox_api_url=env.first_url,
            proxbox_api_key=API_KEY,
            name="n",
            vmid=100,
            target_node="node1",
            image_url="https://images.example.invalid/base.img",
            user_data_yaml="#cloud-config\n",
            endpoint_id=1,
            timeout=5,
            verify_ssl=verify_ssl,
        )
    with pytest.raises(client.ProxboxApiError) as provision_error:
        client.call_proxbox_vm_provision(
            proxbox_api_url=env.first_url,
            proxbox_api_key=API_KEY,
            endpoint_id=1,
            template_vmid=100,
            new_vmid=101,
            new_name="vm",
            target_node="node1",
            cloud_init={},
            timeout=5,
            verify_ssl=verify_ssl,
        )

    for error in (build_error.value, provision_error.value):
        assert API_KEY not in str(error)
        assert "127.0.0.1" not in str(error)
    assert env.second.requests == []
    assert [key for _path, key in env.first.requests] == [API_KEY, API_KEY]


def test_a_normal_response_still_works_through_the_opener(backends) -> None:
    env = backends
    env.state["redirect"] = None

    assert _post(env, True) == {}
    assert env.first.requests == [("/cloud/templates/images", API_KEY)]
