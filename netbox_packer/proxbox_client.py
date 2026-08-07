"""Minimal stdlib HTTP client for delegating cloud-init template image bakes.

netbox-packer does not perform Proxmox operations itself. For ``cloud_config``
installer configs it delegates the real bake to the proxbox-api backend's
``POST /cloud/templates/images`` endpoint, which writes the supplied
``#cloud-config`` as a Proxmox ``cicustom`` user snippet over SSH and converts
the result into a VM template.

Only the Python standard library is used so the plugin keeps zero extra runtime
dependencies beyond NetBox itself.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_RESPONSE_READ_SECONDS = 30.0
_RESPONSE_READ_CHUNK_BYTES = 64 * 1024
_EXECUTED_BUILD_STATUSES = frozenset({"completed", "failed", "recovery_required"})
_PROVISION_STATUSES = frozenset({"started", "stopped"})


class ProxboxApiError(RuntimeError):
    """Raised when the proxbox-api template-image build call fails."""


class ProxboxOperationRecoveryRequired(ProxboxApiError):
    """Raised when execution may have started but no terminal response is known."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__("proxbox-api execution outcome is unknown; operation recovery is required.")


class _ProxboxRequestRejected(ProxboxApiError):
    """An explicit HTTP response proving the requested operation was rejected."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep the proxbox-api key on the configured origin by rejecting redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl) -> None:
        return None


# Ignore HTTP(S)_PROXY and related process environment variables. A proxy is a
# second origin, so forwarding the proxbox-api key to one would violate the
# configured-origin credential boundary even when the target URL is valid.
_NO_REDIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects())


def normalize_proxbox_api_base_url(configured_url: str) -> str:
    """Return one canonical HTTPS origin (or loopback HTTP) before network access."""
    if not isinstance(configured_url, str) or not configured_url:
        raise ProxboxApiError("Invalid proxbox-api base URL: configure one absolute HTTP(S) origin.")
    if configured_url != configured_url.strip() or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in configured_url
    ):
        raise ProxboxApiError("Invalid proxbox-api base URL: whitespace and control characters are not allowed.")
    if "\\" in configured_url:
        raise ProxboxApiError("Invalid proxbox-api base URL: backslashes are not allowed.")

    try:
        parsed = urllib.parse.urlsplit(configured_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ProxboxApiError("Invalid proxbox-api base URL: hostname or port is malformed.") from None

    if parsed.scheme not in {"http", "https"}:
        raise ProxboxApiError("Invalid proxbox-api base URL: scheme must be http or https.")
    if not parsed.netloc or not hostname:
        raise ProxboxApiError("Invalid proxbox-api base URL: hostname is required.")
    if not parsed.netloc.isascii() or "%" in parsed.netloc:
        raise ProxboxApiError("Invalid proxbox-api base URL: the authority must use an unambiguous ASCII form.")
    if parsed.username is not None or parsed.password is not None:
        raise ProxboxApiError("Invalid proxbox-api base URL: embedded credentials are not allowed.")
    if parsed.netloc.endswith(":") or port == 0:
        raise ProxboxApiError("Invalid proxbox-api base URL: port must be between 1 and 65535.")
    if parsed.path not in {"", "/"}:
        raise ProxboxApiError("Invalid proxbox-api base URL: it must not include a path.")
    if "?" in configured_url:
        raise ProxboxApiError("Invalid proxbox-api base URL: it must not include a query string.")
    if "#" in configured_url:
        raise ProxboxApiError("Invalid proxbox-api base URL: it must not include a fragment.")
    if parsed.scheme == "http":
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ProxboxApiError(
                "Invalid proxbox-api base URL: HTTPS is required unless the host is a literal loopback address."
            )

    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _validate_proxbox_api_key(proxbox_api_key: str) -> None:
    """Reject unset or unsafe header values without including the key in errors."""
    if (
        not isinstance(proxbox_api_key, str)
        or not proxbox_api_key
        or len(proxbox_api_key) > 4096
        or proxbox_api_key != proxbox_api_key.strip()
        or not proxbox_api_key.isascii()
        or any(ord(character) < 33 or ord(character) == 127 for character in proxbox_api_key)
    ):
        raise ProxboxApiError("Invalid proxbox-api key: configure a nonempty header-safe ASCII value.")


def _open_request(request: urllib.request.Request, *, timeout: int) -> Any:
    """Open a proxbox-api request without installing redirect behavior globally."""
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _set_response_socket_timeout(response: Any, seconds: float) -> None:
    """Apply the remaining body deadline to urllib's underlying socket when present."""
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None and hasattr(sock, "settimeout"):
        sock.settimeout(max(seconds, 0.001))


def _read_bounded_response(response: Any, *, request_timeout: int) -> bytes:
    """Read a proxbox-api response within fixed size and post-header time limits."""
    getheader = getattr(response, "getheader", None)
    content_length = getheader("Content-Length") if callable(getheader) else None
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            raise ProxboxApiError("proxbox-api returned invalid response framing.") from None
        if declared_length < 0:
            raise ProxboxApiError("proxbox-api returned invalid response framing.")
        if declared_length > _MAX_RESPONSE_BYTES:
            raise ProxboxApiError("proxbox-api response exceeded the allowed size.")

    read_deadline = time.monotonic() + min(max(float(request_timeout), 0.001), _MAX_RESPONSE_READ_SECONDS)
    remaining = _MAX_RESPONSE_BYTES + 1
    chunks: list[bytes] = []
    while remaining:
        seconds_left = read_deadline - time.monotonic()
        if seconds_left <= 0:
            raise ProxboxApiError("proxbox-api response body timed out.")
        _set_response_socket_timeout(response, seconds_left)
        chunk = response.read(min(_RESPONSE_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)

    body = b"".join(chunks)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ProxboxApiError("proxbox-api response exceeded the allowed size.")
    return body


def _post_json(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    path: str,
    payload: dict[str, object],
    timeout: int,
    expected_status: int,
) -> dict[str, object]:
    """POST JSON to proxbox-api and return the decoded response body."""
    base = normalize_proxbox_api_base_url(proxbox_api_url)
    _validate_proxbox_api_key(proxbox_api_key)
    url = f"{base}{path}"
    try:
        data = json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Proxbox-API-Key": proxbox_api_key,
            },
        )
        # nosec B310 - the base URL is restricted above to one explicit HTTP(S) origin.
        with _open_request(request, timeout=timeout) as resp:  # noqa: S310
            response_status = getattr(resp, "status", None)
            if response_status is None:
                getcode = getattr(resp, "getcode", None)
                response_status = getcode() if callable(getcode) else None
            if type(response_status) is not int or response_status != expected_status:
                raise ProxboxApiError("proxbox-api returned an unexpected HTTP success status.")
            body = _read_bounded_response(resp, request_timeout=timeout)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if 300 <= status < 400:
            raise _ProxboxRequestRejected(
                f"proxbox-api refused HTTP redirect ({status}); configure the canonical backend base URL."
            ) from None
        raise _ProxboxRequestRejected(f"proxbox-api request failed with HTTP {status}.") from None
    except ProxboxApiError:
        raise
    except Exception:
        raise ProxboxApiError("proxbox-api request failed before a valid response was received.") from None

    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProxboxApiError("proxbox-api returned an invalid JSON response.") from None
    if not isinstance(decoded, dict):
        raise ProxboxApiError("proxbox-api returned an invalid JSON object response.")
    return decoded


def _get_json(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    path: str,
    timeout: int,
    expected_status: int,
) -> dict[str, object]:
    """GET bounded JSON from the configured proxbox-api origin."""
    base = normalize_proxbox_api_base_url(proxbox_api_url)
    _validate_proxbox_api_key(proxbox_api_key)
    request = urllib.request.Request(
        f"{base}{path}",
        method="GET",
        headers={"Accept": "application/json", "X-Proxbox-API-Key": proxbox_api_key},
    )
    try:
        # nosec B310 - the base URL is restricted above to one explicit HTTP(S) origin.
        with _open_request(request, timeout=timeout) as resp:  # noqa: S310
            response_status = getattr(resp, "status", None)
            if response_status is None:
                getcode = getattr(resp, "getcode", None)
                response_status = getcode() if callable(getcode) else None
            if type(response_status) is not int or response_status != expected_status:
                raise ProxboxApiError("proxbox-api returned an unexpected HTTP success status.")
            body = _read_bounded_response(resp, request_timeout=timeout)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if 300 <= status < 400:
            raise _ProxboxRequestRejected(
                f"proxbox-api refused HTTP redirect ({status}); configure the canonical backend base URL."
            ) from None
        raise _ProxboxRequestRejected(f"proxbox-api request failed with HTTP {status}.") from None
    except ProxboxApiError:
        raise
    except Exception:
        raise ProxboxApiError("proxbox-api request failed before a valid response was received.") from None

    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProxboxApiError("proxbox-api returned an invalid JSON response.") from None
    if not isinstance(decoded, dict):
        raise ProxboxApiError("proxbox-api returned an invalid JSON object response.")
    return decoded


def validate_proxbox_target_node(value: str) -> str:
    """Return a target node matching the proxbox-api v0.0.21 contract."""
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None:
        raise ValueError("target node does not match the proxbox-api contract")
    return value


def validate_proxbox_storage_identifier(value: str) -> str:
    """Return storage matching proxbox-api v0.0.21's exact identifier contract."""
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value) is None:
        raise ValueError("storage does not match the proxbox-api contract")
    return value


def _response_int(response: dict[str, object], field: str, *, endpoint: str) -> int:
    """Return one exact integer response field, rejecting booleans and coercion."""
    value = response.get(field)
    if type(value) is not int:  # bool is an int subclass and is not a valid VMID/return code.
        raise ProxboxApiError(f"proxbox-api returned an invalid {endpoint} response.")
    return value


def _response_matches_build_target(
    response: dict[str, object],
    *,
    expected_endpoint_id: int,
    expected_target_node: str,
    expected_vmid: int,
) -> bool:
    """Return whether a v2 response remains bound to the requested target."""
    return (
        response.get("contract_version") == "2.0"
        and type(response.get("endpoint_id")) is int
        and response.get("endpoint_id") == expected_endpoint_id
        and isinstance(response.get("target_node"), str)
        and response.get("target_node") == expected_target_node
        and type(response.get("vmid")) is int
        and response.get("vmid") == expected_vmid
        and (
            response.get("template_vmid") is None
            or (type(response.get("template_vmid")) is int and response.get("template_vmid") == expected_vmid)
        )
    )


def _validate_build_plan_response(
    response: dict[str, object],
    *,
    expected_endpoint_id: int,
    expected_target_node: str,
    expected_vmid: int,
) -> dict[str, object]:
    """Validate the secret-safe v2 planning response needed for preflight."""
    recipe_digest = response.get("recipe_digest")
    if (
        not _response_matches_build_target(
            response,
            expected_endpoint_id=expected_endpoint_id,
            expected_target_node=expected_target_node,
            expected_vmid=expected_vmid,
        )
        or response.get("status") != "planned"
        or not isinstance(recipe_digest, str)
        or len(recipe_digest) != 64
        or any(character not in "0123456789abcdef" for character in recipe_digest)
    ):
        raise ProxboxApiError("proxbox-api returned an invalid template-build plan response.")
    return {
        "status": "planned",
        "vmid": expected_vmid,
        "template_vmid": expected_vmid,
        "recipe_digest": recipe_digest,
    }


def _validate_build_preflight_response(
    response: dict[str, object],
    *,
    expected_endpoint_id: int,
    expected_target_node: str,
    expected_vmid: int,
    expected_recipe_digest: str,
) -> tuple[str, str]:
    """Return the signed token and operation UUID from an exact ready preflight."""
    if response.get("ready") is not True or response.get("writes_enabled") is not True:
        raise ProxboxApiError("proxbox-api preflight denied the executable template build.")
    plan_token = response.get("plan_token")
    plan_id = response.get("plan_id")
    if (
        response.get("contract_version") != "1.0"
        or type(response.get("endpoint_id")) is not int
        or response.get("endpoint_id") != expected_endpoint_id
        or not isinstance(response.get("target_node"), str)
        or response.get("target_node") != expected_target_node
        or type(response.get("vmid")) is not int
        or response.get("vmid") != expected_vmid
        or response.get("recipe_digest") != expected_recipe_digest
        or not isinstance(plan_id, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            plan_id,
        )
        is None
        or not isinstance(plan_token, str)
        or len(plan_token) < 64
        or len(plan_token) > 4096
        or not plan_token.isascii()
        or plan_token != plan_token.strip()
        or any(ord(character) < 33 or ord(character) == 127 for character in plan_token)
    ):
        raise ProxboxApiError("proxbox-api returned an invalid template-build preflight response.")
    return plan_token, plan_id


def _validate_build_response(
    response: dict[str, object],
    *,
    expected_endpoint_id: int,
    expected_target_node: str,
    expected_vmid: int,
    expected_recipe_digest: str,
    expected_operation_id: str,
) -> dict[str, object]:
    """Validate and minimize an executed v2 build response before persistence."""
    status = response.get("status")
    if (
        not _response_matches_build_target(
            response,
            expected_endpoint_id=expected_endpoint_id,
            expected_target_node=expected_target_node,
            expected_vmid=expected_vmid,
        )
        or not isinstance(status, str)
        or status not in _EXECUTED_BUILD_STATUSES
        or response.get("recipe_digest") != expected_recipe_digest
        or response.get("operation_id") != expected_operation_id
    ):
        raise ProxboxApiError("proxbox-api returned an invalid template-build response.")

    response_vmid = _response_int(response, "vmid", endpoint="template-build")
    if response_vmid < 100 or response_vmid != expected_vmid:
        raise ProxboxApiError("proxbox-api returned an invalid template-build response.")

    template_vmid = response.get("template_vmid")
    if template_vmid is not None and (type(template_vmid) is not int or template_vmid != expected_vmid):
        raise ProxboxApiError("proxbox-api returned an invalid template-build response.")

    verified = response.get("verified")
    recovery_required = response.get("recovery_required")
    if type(verified) is not bool or type(recovery_required) is not bool:
        raise ProxboxApiError("proxbox-api returned an invalid template-build response.")

    returncode = response.get("returncode")
    if returncode is not None and type(returncode) is not int:
        raise ProxboxApiError("proxbox-api returned an invalid template-build response.")
    if status == "completed":
        if not verified or recovery_required or returncode != 0:
            raise ProxboxApiError("proxbox-api returned an inconsistent template-build response.")
    elif status == "recovery_required":
        if verified or not recovery_required:
            raise ProxboxApiError("proxbox-api returned an inconsistent template-build response.")
    elif verified or recovery_required or returncode == 0:
        raise ProxboxApiError("proxbox-api returned an inconsistent template-build response.")

    operation_id = response.get("operation_id")
    if (
        not isinstance(operation_id, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            operation_id,
        )
        is None
    ):
        raise ProxboxApiError("proxbox-api returned an invalid template-build response.")

    # Deliberately discard build_script, commands, generated user data, stdout,
    # stderr, and every other backend-controlled field. They can contain
    # credentials supplied by Packer, cloud-init, or the remote shell and must
    # never reach a durable NetBox build record.
    validated: dict[str, object] = {
        "status": status,
        "vmid": response_vmid,
        "operation_id": operation_id,
        "verified": verified,
        "recovery_required": recovery_required,
    }
    if template_vmid is not None:
        validated["template_vmid"] = template_vmid
    if returncode is not None:
        validated["returncode"] = returncode
    return validated


def _validate_build_operation_response(
    response: dict[str, object],
    *,
    expected_endpoint_id: int,
    expected_target_node: str,
    expected_vmid: int,
    expected_recipe_digest: str,
    expected_operation_id: str,
) -> dict[str, object]:
    """Validate one secret-free durable operation lookup after an ambiguous POST."""
    state = response.get("state")
    verified = response.get("verified")
    recovery_required = response.get("recovery_required")
    if (
        response.get("operation_id") != expected_operation_id
        or type(response.get("endpoint_id")) is not int
        or response.get("endpoint_id") != expected_endpoint_id
        or response.get("target_node") != expected_target_node
        or type(response.get("vmid")) is not int
        or response.get("vmid") != expected_vmid
        or response.get("provider") != "release_image"
        or response.get("recipe_digest") != expected_recipe_digest
        or state not in {"leased", "running", "completed", "failed", "cancelled", "recovery_required"}
        or type(verified) is not bool
        or type(recovery_required) is not bool
    ):
        raise ProxboxApiError("proxbox-api returned an invalid template-build operation response.")
    if state == "completed" and (not verified or recovery_required):
        raise ProxboxApiError("proxbox-api returned an inconsistent template-build operation response.")
    if state == "recovery_required" and (verified or not recovery_required):
        raise ProxboxApiError("proxbox-api returned an inconsistent template-build operation response.")
    if state in {"leased", "running", "failed", "cancelled"} and (verified or recovery_required):
        raise ProxboxApiError("proxbox-api returned an inconsistent template-build operation response.")
    return {
        "state": state,
        "verified": verified,
        "recovery_required": recovery_required,
        "operation_id": expected_operation_id,
        "vmid": expected_vmid,
    }


def _recover_ambiguous_build_response(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    timeout: int,
    endpoint_id: int,
    target_node: str,
    vmid: int,
    recipe_digest: str,
    operation_id: str,
) -> dict[str, object]:
    """Resolve a lost execution response through the durable operation journal."""
    try:
        operation = _validate_build_operation_response(
            _get_json(
                proxbox_api_url=proxbox_api_url,
                proxbox_api_key=proxbox_api_key,
                path=f"/cloud/templates/images/operations/{operation_id}",
                timeout=timeout,
                expected_status=200,
            ),
            expected_endpoint_id=endpoint_id,
            expected_target_node=target_node,
            expected_vmid=vmid,
            expected_recipe_digest=recipe_digest,
            expected_operation_id=operation_id,
        )
    except ProxboxApiError:
        raise ProxboxOperationRecoveryRequired(operation_id) from None

    state = str(operation["state"])
    if state in {"leased", "running"}:
        raise ProxboxOperationRecoveryRequired(operation_id)
    if state == "completed":
        return {
            "status": "completed",
            "vmid": vmid,
            "template_vmid": vmid,
            "operation_id": operation_id,
            "verified": True,
            "recovery_required": False,
            "returncode": 0,
        }
    if state == "recovery_required" or operation["recovery_required"]:
        return {
            "status": "recovery_required",
            "vmid": vmid,
            "template_vmid": vmid,
            "operation_id": operation_id,
            "verified": False,
            "recovery_required": True,
        }
    return {
        "status": "failed",
        "vmid": vmid,
        "template_vmid": vmid,
        "operation_id": operation_id,
        "verified": False,
        "recovery_required": False,
        "returncode": 1,
    }


def _validate_provision_response(
    response: dict[str, object],
    *,
    expected_vmid: int,
    expected_started: bool,
) -> dict[str, object]:
    """Validate and minimize a VM-provision response for the UI caller."""
    response_vmid = _response_int(response, "new_vmid", endpoint="VM-provision")
    expected_status = "started" if expected_started else "stopped"
    status = response.get("status")
    if (
        response_vmid < 100
        or response_vmid != expected_vmid
        or not isinstance(status, str)
        or status not in _PROVISION_STATUSES
        or status != expected_status
    ):
        raise ProxboxApiError("proxbox-api returned an invalid VM-provision response.")
    return {"new_vmid": response_vmid, "status": status}


def call_proxbox_build(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    name: str,
    vmid: int,
    target_node: str | None,
    image_url: str,
    user_data_yaml: str,
    endpoint_id: int | None = None,
    image_storage: str = "local",
    vm_storage: str = "local",
    storage: str = "local",
    snippets_storage: str = "local",
    memory_mb: int = 2048,
    cores: int = 2,
    bridge: str = "vmbr0",
    execute: bool = True,
    timeout: int = 3900,
    operation_planned: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """POST to ``{proxbox_api_url}/cloud/templates/images`` and return safe result metadata.

    Raises :class:`ProxboxApiError` on transport, HTTP, or response-contract
    failure. Script and process-output fields are discarded at this boundary.
    """
    if type(endpoint_id) is not int or endpoint_id < 1 or not isinstance(target_node, str):
        raise ProxboxApiError("Executable template builds require a positive endpoint_id and nonempty target_node.")
    try:
        target_node = validate_proxbox_target_node(target_node)
    except ValueError:
        raise ProxboxApiError(
            "Executable template builds require a positive endpoint_id and nonempty target_node."
        ) from None
    try:
        image_storage = validate_proxbox_storage_identifier(image_storage)
        vm_storage = validate_proxbox_storage_identifier(vm_storage)
        storage = validate_proxbox_storage_identifier(storage)
        snippets_storage = validate_proxbox_storage_identifier(snippets_storage)
    except ValueError:
        raise ProxboxApiError("Template build storage does not match the producer contract.") from None
    if storage != vm_storage:
        raise ProxboxApiError("storage and vm_storage must match during the proxbox-api v0.0.21 transition.")

    payload: dict[str, object] = {
        "name": name,
        "vmid": vmid,
        "target_node": target_node,
        "image_url": image_url,
        "user_data_yaml": user_data_yaml,
        "image_storage": image_storage,
        "vm_storage": vm_storage,
        "storage": storage,
        "snippets_storage": snippets_storage,
        "memory_mb": memory_mb,
        "cores": cores,
        "bridge": bridge,
        "provider": "release_image",
        "endpoint_id": endpoint_id,
        "include_sensitive_preview": False,
    }

    plan = _validate_build_plan_response(
        _post_json(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            path="/cloud/templates/images",
            payload={**payload, "execute": False},
            timeout=timeout,
            expected_status=201,
        ),
        expected_endpoint_id=endpoint_id,
        expected_target_node=target_node,
        expected_vmid=vmid,
    )
    if not execute:
        return {key: value for key, value in plan.items() if key != "recipe_digest"}

    recipe_digest = str(plan["recipe_digest"])
    plan_token, plan_id = _validate_build_preflight_response(
        _post_json(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            path="/cloud/templates/images/preflight",
            payload={
                "contract_version": "1.0",
                "endpoint_id": endpoint_id,
                "target_node": target_node,
                "vmid": vmid,
                "provider": "release_image",
                "image_storage": image_storage,
                "vm_storage": vm_storage,
                "snippets_storage": snippets_storage,
                "recipe_digest": recipe_digest,
            },
            timeout=timeout,
            expected_status=200,
        ),
        expected_endpoint_id=endpoint_id,
        expected_target_node=target_node,
        expected_vmid=vmid,
        expected_recipe_digest=recipe_digest,
    )
    if operation_planned is not None:
        try:
            operation_planned(plan_id)
        except Exception:
            raise ProxboxApiError("Could not durably record the planned proxbox-api operation.") from None

    try:
        return _validate_build_response(
            _post_json(
                proxbox_api_url=proxbox_api_url,
                proxbox_api_key=proxbox_api_key,
                path="/cloud/templates/images",
                payload={**payload, "execute": True, "preflight_plan_token": plan_token},
                timeout=timeout,
                expected_status=201,
            ),
            expected_endpoint_id=endpoint_id,
            expected_target_node=target_node,
            expected_vmid=vmid,
            expected_recipe_digest=recipe_digest,
            expected_operation_id=plan_id,
        )
    except _ProxboxRequestRejected:
        raise
    except ProxboxApiError:
        return _recover_ambiguous_build_response(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            timeout=timeout,
            endpoint_id=endpoint_id,
            target_node=target_node,
            vmid=vmid,
            recipe_digest=recipe_digest,
            operation_id=plan_id,
        )


def call_proxbox_vm_provision(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    endpoint_id: int,
    template_vmid: int,
    new_vmid: int,
    new_name: str,
    target_node: str,
    cloud_init: dict,
    start_after_provision: bool = True,
    storage: str | None = None,
    memory_mb: int | None = None,
    cores: int | None = None,
    full_clone: bool = True,
    timeout: int = 90,
) -> dict[str, object]:
    """POST to ``{proxbox_api_url}/cloud/vm/provision`` and return safe result metadata."""
    payload: dict[str, object] = {
        "endpoint_id": int(endpoint_id),
        "template_vmid": int(template_vmid),
        "new_vmid": int(new_vmid),
        "new_name": new_name,
        "target_node": target_node,
        "cloud_init": cloud_init,
        "start_after_provision": bool(start_after_provision),
        "storage": storage,
        "memory_mb": memory_mb,
        "cores": cores,
        "full_clone": bool(full_clone),
    }
    return _validate_provision_response(
        _post_json(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            path="/cloud/vm/provision",
            payload=payload,
            timeout=timeout,
            expected_status=200,
        ),
        expected_vmid=int(new_vmid),
        expected_started=bool(start_after_provision),
    )
