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
import urllib.error
import urllib.request


class ProxboxApiError(RuntimeError):
    """Raised when the proxbox-api template-image build call fails."""


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
        raise ProxboxApiError(f"proxbox-api HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise ProxboxApiError(f"proxbox-api unreachable at {url}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProxboxApiError(f"proxbox-api returned non-JSON body: {body[:300]}") from exc


def call_proxbox_build(
    *,
    proxbox_api_url: str,
    proxbox_api_key: str,
    name: str,
    vmid: int,
    target_node: str,
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
    ssh_host: str | None = None,
    ssh_user: str = "root",
    ssh_port: int = 22,
    ssh_identity_file: str | None = None,
    execute: bool = True,
    timeout: int = 3900,
) -> dict:
    """POST to ``{proxbox_api_url}/cloud/templates/images`` and return the JSON body.

    Raises :class:`ProxboxApiError` on transport or HTTP failure. The response is
    the proxbox-api ``CloudImageTemplateBuildResponse`` (carries ``status``,
    ``vmid``/``template_vmid``, ``build_script``, ``stdout``/``stderr``).
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
        "execute": execute,
    }
    if endpoint_id is not None:
        payload["endpoint_id"] = endpoint_id
    if ssh_host:
        payload["ssh_host"] = ssh_host
        payload["ssh_user"] = ssh_user
        payload["ssh_port"] = ssh_port
    if ssh_identity_file:
        payload["ssh_identity_file"] = ssh_identity_file

    return _post_json(
        proxbox_api_url=proxbox_api_url,
        proxbox_api_key=proxbox_api_key,
        path="/cloud/templates/images",
        payload=payload,
        timeout=timeout,
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
