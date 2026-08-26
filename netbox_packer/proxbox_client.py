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

import json
import math
import time
import urllib.error
import urllib.request


class ProxboxApiError(RuntimeError):
    """Raised when the proxbox-api template-image build call fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def _error_code_from_body(body: str) -> str | None:
    """Extract proxbox-api's stable error code from an HTTP response body."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    detail = parsed.get("detail", parsed)
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    return str(code) if code else None


def _post_json(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    path: str,
    payload: dict,
    timeout: int,
) -> dict:
    """POST JSON to proxbox-api and return the decoded response body."""
    base = proxbox_api_url.rstrip("/")
    url = f"{base}{path}"
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
    try:
        # nosec B310 - scheme is operator-configured http(s) from plugin settings
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ProxboxApiError(
            f"proxbox-api HTTP {exc.code}: {detail[:500]}",
            status_code=exc.code,
            error_code=_error_code_from_body(detail),
        ) from exc
    except urllib.error.URLError as exc:
        raise ProxboxApiError(f"proxbox-api unreachable at {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProxboxApiError(f"proxbox-api timed out at {url} after {timeout}s") from exc

    try:
        response = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProxboxApiError(f"proxbox-api returned non-JSON body: {body[:300]}") from exc
    if not isinstance(response, dict):
        raise ProxboxApiError("proxbox-api returned JSON that is not an object")
    return response


def _preflight_findings_summary(response: dict) -> str:
    """Render bounded, actionable preflight findings for the persisted build log."""
    findings = response.get("findings")
    if not isinstance(findings, list) or not findings:
        return "no findings returned"

    rendered: list[str] = []
    for finding in findings[:10]:
        if not isinstance(finding, dict):
            rendered.append(str(finding)[:500])
            continue
        code = str(finding.get("code") or "unknown")
        severity = str(finding.get("severity") or "unknown")
        target = str(finding.get("target") or "target-unspecified")
        message = str(finding.get("message") or "no message")
        rendered.append(f"{code} [{severity}] {target}: {message}"[:500])
    if len(findings) > 10:
        rendered.append(f"{len(findings) - 10} additional finding(s) omitted")
    return "; ".join(rendered)


def _raise_execute_error(exc: ProxboxApiError) -> None:
    """Translate signed-plan rejection codes into actionable build failures."""
    if exc.error_code == "preflight_plan_expired":
        message = (
            "proxbox-api rejected the signed preflight plan because it expired before "
            "execution; start a fresh build to obtain a new plan"
        )
    elif exc.error_code == "preflight_plan_mismatch":
        message = (
            "proxbox-api rejected the signed preflight plan because the execute request "
            "did not match the server-rendered plan; refusing execution"
        )
    else:
        raise exc
    raise ProxboxApiError(
        message,
        status_code=exc.status_code,
        error_code=exc.error_code,
    ) from exc


def _validate_executed_response(response: dict) -> None:
    """Fail unless proxbox-api confirms execution and final artifact verification."""
    execution = response.get("execution")
    execution_ok = (
        isinstance(execution, dict)
        and execution.get("attempted") is True
        and execution.get("enabled") is True
        and execution.get("exit_code") == 0
    )
    if (
        str(response.get("status") or "").lower() == "completed"
        and response.get("verified") is True
        and response.get("execution_enabled") is True
        and execution_ok
    ):
        return

    diagnostics = response.get("diagnostics")
    detail = _preflight_findings_summary({"findings": diagnostics})
    raise ProxboxApiError(
        "proxbox-api did not confirm an executed, verified cloud-image build; "
        f"status={response.get('status') or 'unknown'}, diagnostics: {detail}"
    )


def call_proxbox_build(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    name: str,
    vmid: int,
    target_node: str | None,
    image_url: str,
    user_data_yaml: str,
    image_sha256: str = "",
    endpoint_id: int | None = None,
    image_storage: str = "local",
    vm_storage: str = "local",
    storage: str = "local",
    snippets_storage: str = "local",
    memory_mb: int = 2048,
    cores: int = 2,
    bridge: str = "vmbr0",
    ssh_host: str | None = None,
    ssh_user: str = "root",
    ssh_port: int = 22,
    ssh_identity_file: str | None = None,
    execute: bool = True,
    timeout: int = 3900,
) -> dict:
    """Plan, preflight, and execute a proxbox-api cloud-image build.

    Executable builds use the server-rendered recipe digest to obtain a signed,
    expiring preflight plan, then present that plan without changing any build
    fields. Explicit ``execute=False`` calls retain the existing plan-only
    behavior. Raises :class:`ProxboxApiError` on any transport, contract,
    preflight, expiry, execution, or artifact-verification failure.
    """
    payload: dict = {
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
    }
    if image_sha256:
        # proxbox-api's CloudImageTemplateBuildRequest verifies the downloaded image
        # against this digest. Omitted entirely when empty, so an unpinned build keeps
        # a byte-for-byte identical payload.
        payload["sha256"] = image_sha256
    if endpoint_id is not None:
        payload["endpoint_id"] = endpoint_id
    if ssh_host:
        payload["ssh_host"] = ssh_host
        payload["ssh_user"] = ssh_user
        payload["ssh_port"] = ssh_port
    if ssh_identity_file:
        payload["ssh_identity_file"] = ssh_identity_file

    if not execute:
        return _post_json(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            path="/cloud/templates/images",
            payload={**payload, "execute": False},
            timeout=timeout,
        )

    if endpoint_id is None:
        raise ProxboxApiError(
            "proxbox-api signed preflight requires the backend endpoint id; "
            "netbox-packer could not resolve it from the authorized selected endpoint"
        )
    if not target_node:
        raise ProxboxApiError(
            "proxbox-api signed preflight requires target_node; select a Proxmox node "
            "or pass variable_overrides['target_node'] for this build"
        )

    plan_payload = {**payload, "execute": False}
    plan = _post_json(
        proxbox_api_url=proxbox_api_url,
        proxbox_api_key=proxbox_api_key,
        path="/cloud/templates/images",
        payload=plan_payload,
        timeout=timeout,
    )
    recipe_digest = plan.get("recipe_digest")
    if (
        not isinstance(recipe_digest, str)
        or len(recipe_digest) != 64
        or any(character not in "0123456789abcdef" for character in recipe_digest)
    ):
        raise ProxboxApiError(
            "incompatible proxbox-api: the non-executing build plan did not return "
            "a valid server-authored recipe_digest; refusing legacy one-step execution"
        )

    preflight_payload = {
        "contract_version": "1.0",
        "endpoint_id": endpoint_id,
        "target_node": target_node,
        "vmid": vmid,
        "provider": "release_image",
        "image_storage": image_storage,
        "vm_storage": vm_storage,
        "snippets_storage": snippets_storage,
        "recipe_digest": recipe_digest,
        "snippets_required": True,
    }
    try:
        preflight = _post_json(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            path="/cloud/templates/images/preflight",
            payload=preflight_payload,
            timeout=timeout,
        )
    except ProxboxApiError as exc:
        if exc.status_code == 404:
            raise ProxboxApiError(
                "incompatible proxbox-api: signed-preflight endpoint "
                "/cloud/templates/images/preflight is unavailable (HTTP 404); upgrade "
                "proxbox-api because legacy one-step execution is intentionally disabled",
                status_code=exc.status_code,
                error_code=exc.error_code,
            ) from exc
        raise ProxboxApiError(
            f"proxbox-api signed preflight failed: {exc}",
            status_code=exc.status_code,
            error_code=exc.error_code,
        ) from exc

    if preflight.get("ready") is not True:
        raise ProxboxApiError(
            "proxbox-api signed preflight did not report ready=true; findings: "
            + _preflight_findings_summary(preflight)
        )
    if preflight.get("writes_enabled") is not True:
        raise ProxboxApiError(
            "proxbox-api signed preflight reported writes_enabled=false; enable writes "
            "for the selected ProxmoxEndpoint before rebuilding"
        )
    if preflight.get("recipe_digest") != recipe_digest:
        raise ProxboxApiError(
            "proxbox-api signed preflight returned a recipe_digest that does not match "
            "the server-rendered build plan; refusing execution"
        )

    plan_token = preflight.get("plan_token")
    if not isinstance(plan_token, str) or not plan_token:
        raise ProxboxApiError("proxbox-api signed preflight returned ready=true but no plan_token; refusing execution")
    expires_at = preflight.get("expires_at")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        raise ProxboxApiError(
            "proxbox-api signed preflight returned a plan_token without a valid expires_at; refusing execution"
        )
    expires_at = float(expires_at)
    if not math.isfinite(expires_at) or expires_at <= time.time():
        raise ProxboxApiError(
            "proxbox-api signed preflight plan expired before execution; start a fresh build to obtain a new plan"
        )

    execute_payload = {
        **payload,
        "execute": True,
        "preflight_plan_token": plan_token,
    }
    try:
        response = _post_json(
            proxbox_api_url=proxbox_api_url,
            proxbox_api_key=proxbox_api_key,
            path="/cloud/templates/images",
            payload=execute_payload,
            timeout=timeout,
        )
    except ProxboxApiError as exc:
        _raise_execute_error(exc)
        raise  # pragma: no cover - _raise_execute_error either raises exc or a translation

    _validate_executed_response(response)
    return response


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
) -> dict:
    """POST to ``{proxbox_api_url}/cloud/vm/provision`` and return the JSON body."""
    payload: dict = {
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
    return _post_json(
        proxbox_api_url=proxbox_api_url,
        proxbox_api_key=proxbox_api_key,
        path="/cloud/vm/provision",
        payload=payload,
        timeout=timeout,
    )
