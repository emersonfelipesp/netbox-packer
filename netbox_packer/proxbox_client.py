"""HTTP client for proxbox-api image factory endpoints."""

import json
import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger("netbox_packer.proxbox_client")

PROXBOX_TO_PACKER_STATUS = {
    "completed": "success",
    "failed": "failed",
    "cancelled": "cancelled",
    "running": "running",
    "queued": "queued",
}


class ProxboxAPIError(Exception):
    """Raised when proxbox-api returns a non-success response."""

    def __init__(self, status_code, detail=""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"proxbox-api HTTP {status_code}: {detail}")


@dataclass
class SSEEvent:
    event: str
    data: dict | str


def _headers(api_key):
    h = {"Content-Type": "application/json"}
    if api_key:
        h["X-Proxbox-API-Key"] = api_key
    return h


def _check(resp):
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise ProxboxAPIError(resp.status_code, detail)


def resolve_endpoint_id(proxbox_url, proxmox_endpoint_url):
    """Resolve a Proxmox endpoint URL to its integer endpoint_id on proxbox-api."""
    resp = requests.get(
        f"{proxbox_url.rstrip('/')}/cloud/proxmox-endpoint/by-url",
        params={"url": proxmox_endpoint_url},
        timeout=30,
    )
    _check(resp)
    data = resp.json()
    return data["endpoint_id"]


def start_build(proxbox_url, api_key, payload):
    """POST to proxbox-api to start an image factory build. Returns build_id."""
    resp = requests.post(
        f"{proxbox_url.rstrip('/')}/cloud/image-factory/builds",
        headers=_headers(api_key),
        json=payload,
        timeout=60,
    )
    _check(resp)
    data = resp.json()
    return data["build_id"]


def stream_build(proxbox_url, api_key, build_id, timeout=3600):
    """Consume the SSE stream for a build. Yields SSEEvent objects."""
    resp = requests.get(
        f"{proxbox_url.rstrip('/')}/cloud/image-factory/builds/{build_id}/stream",
        headers=_headers(api_key),
        stream=True,
        timeout=timeout,
    )
    _check(resp)

    event_type = "message"
    data_lines = []

    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue

        line = raw_line

        if line == "":
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    parsed = json.loads(raw_data)
                except (json.JSONDecodeError, ValueError):
                    parsed = raw_data
                yield SSEEvent(event=event_type, data=parsed)
            event_type = "message"
            data_lines = []
            continue

        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())


def cancel_build(proxbox_url, api_key, build_id):
    """Request cancellation of a running build on proxbox-api."""
    resp = requests.post(
        f"{proxbox_url.rstrip('/')}/cloud/image-factory/builds/{build_id}/cancel",
        headers=_headers(api_key),
        timeout=30,
    )
    _check(resp)
    return resp.json()


def map_status(proxbox_status):
    """Map a proxbox-api build status to netbox-packer's BuildStatusChoices."""
    return PROXBOX_TO_PACKER_STATUS.get(proxbox_status, proxbox_status)
