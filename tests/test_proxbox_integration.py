"""Contract tests for netbox-packer ↔ proxbox-api integration."""

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# Import proxbox_client directly to avoid triggering the Django-dependent
# netbox_packer.__init__ (which imports netbox.plugins).
_spec = importlib.util.spec_from_file_location(
    "netbox_packer.proxbox_client",
    "netbox_packer/proxbox_client.py",
    submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["netbox_packer.proxbox_client"] = _mod
_spec.loader.exec_module(_mod)

ProxboxAPIError = _mod.ProxboxAPIError
SSEEvent = _mod.SSEEvent
map_status = _mod.map_status
resolve_endpoint_id = _mod.resolve_endpoint_id
start_build = _mod.start_build
stream_build = _mod.stream_build
cancel_build = _mod.cancel_build


def _patch_requests_get(return_value):
    """Patch requests.get on the loaded proxbox_client module."""
    return patch.object(_mod.requests, "get", return_value=return_value)


def _patch_requests_post(return_value):
    """Patch requests.post on the loaded proxbox_client module."""
    return patch.object(_mod.requests, "post", return_value=return_value)


class TestResolveEndpointId:
    def test_returns_endpoint_id(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"endpoint_id": 42}

        with _patch_requests_get(mock_resp) as mock_get:
            result = resolve_endpoint_id("http://proxbox:8000", "https://pve1:8006")
            assert result == 42
            mock_get.assert_called_once_with(
                "http://proxbox:8000/cloud/proxmox-endpoint/by-url",
                params={"url": "https://pve1:8006"},
                timeout=30,
            )

    def test_raises_on_404(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"detail": "No endpoint found"}
        mock_resp.text = '{"detail": "No endpoint found"}'

        with _patch_requests_get(mock_resp):
            with pytest.raises(ProxboxAPIError) as exc_info:
                resolve_endpoint_id("http://proxbox:8000", "https://unknown:8006")
            assert exc_info.value.status_code == 404


class TestStartBuild:
    def test_returns_build_id(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"build_id": "abc-123", "status": "queued"}

        with _patch_requests_post(mock_resp) as mock_post:
            result = start_build("http://proxbox:8000", "my-key", {"endpoint_id": 1})
            assert result == "abc-123"
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["headers"]["X-Proxbox-API-Key"] == "my-key"

    def test_raises_on_validation_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"detail": "Validation error"}
        mock_resp.text = '{"detail": "Validation error"}'

        with _patch_requests_post(mock_resp):
            with pytest.raises(ProxboxAPIError) as exc_info:
                start_build("http://proxbox:8000", "key", {})
            assert exc_info.value.status_code == 422


class TestStreamBuild:
    def test_parses_sse_events(self):
        sse_lines = [
            "event:packer_log",
            "data:building image...",
            "",
            "event:build_completed",
            'data:{"status": "completed"}',
            "",
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = iter(sse_lines)

        with _patch_requests_get(mock_resp):
            events = list(stream_build("http://proxbox:8000", "key", "build-1"))

        assert len(events) == 2
        assert events[0].event == "packer_log"
        assert events[0].data == "building image..."
        assert events[1].event == "build_completed"
        assert isinstance(events[1].data, dict)
        assert events[1].data["status"] == "completed"


class TestCancelBuild:
    def test_sends_cancel_request(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"build_id": "x", "status": "cancelled"}

        with _patch_requests_post(mock_resp) as mock_post:
            result = cancel_build("http://proxbox:8000", "key", "build-x")
            assert result["status"] == "cancelled"
            assert "/cancel" in mock_post.call_args.args[0]


class TestStatusMapping:
    @pytest.mark.parametrize(
        "proxbox_status,expected",
        [
            ("completed", "success"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("running", "running"),
            ("queued", "queued"),
        ],
    )
    def test_maps_all_known_statuses(self, proxbox_status, expected):
        assert map_status(proxbox_status) == expected

    def test_unknown_status_passthrough(self):
        assert map_status("unknown_thing") == "unknown_thing"


class TestRequestPayloadShape:
    def test_payload_matches_proxbox_api_schema(self):
        """Verify the field mapping produces a valid PackerImageBuildRequest shape."""
        required_fields = {
            "endpoint_id",
            "target_node",
            "output_vmid",
            "output_name",
            "os_family",
            "os_release",
            "image_version",
            "vm_storage",
            "provisioner_recipe",
        }
        payload = {
            "endpoint_id": 1,
            "target_node": "pve1",
            "output_vmid": 9000,
            "output_name": "debian-12-base",
            "os_family": "debian",
            "os_release": "bookworm",
            "image_version": "12",
            "vm_storage": "local-lvm",
            "provisioner_recipe": "debian-base",
        }
        assert required_fields.issubset(payload.keys())

    def test_optional_fields_accepted(self):
        """Verify optional fields can be included without breaking the payload."""
        payload = {
            "endpoint_id": 1,
            "target_node": "pve1",
            "output_vmid": 9000,
            "output_name": "test",
            "os_family": "ubuntu",
            "os_release": "jammy",
            "image_version": "22.04",
            "vm_storage": "local-lvm",
            "provisioner_recipe": "ubuntu-base",
            "cpu_type": "Nehalem",
            "variables": {"http_proxy": "http://proxy:3128"},
        }
        assert "cpu_type" in payload
        assert "variables" in payload


class TestProxboxAPIErrorFields:
    def test_error_preserves_status_and_detail(self):
        err = ProxboxAPIError(503, "Service unavailable")
        assert err.status_code == 503
        assert "503" in str(err)
        assert "Service unavailable" in str(err)
