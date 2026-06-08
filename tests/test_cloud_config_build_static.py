"""Tests for the cloud-init template image build path.

Static (text/AST) assertions plus an isolated functional test of
``proxbox_client`` — all run without Django or NetBox installed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "netbox_packer"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── Static wiring assertions ──────────────────────────────────────────────────


def test_plugin_settings_has_proxbox_api_fields() -> None:
    src = _read("netbox_packer/models.py")
    assert "proxbox_api_url = models.URLField(" in src
    assert "proxbox_api_key_encrypted = models.CharField(" in src
    assert "def set_proxbox_api_key(self, plain: str) -> None:" in src
    assert "def get_proxbox_api_key(self) -> str:" in src
    assert "def _fernet():" in src


def test_jobs_branches_on_cloud_config_and_delegates() -> None:
    src = _read("netbox_packer/jobs.py")
    assert 'installer.installer_type == "cloud_config"' in src
    assert "def _run_proxbox_cloud_build(self, build, template, node, timeout):" in src
    assert "from .proxbox_client import ProxboxApiError, call_proxbox_build" in src
    assert "user_data_yaml=installer.content" in src
    # Gap 1: PackerBuild creation must enqueue the job.
    assert "def dispatch_build(build):" in src
    assert "PackerBuildJob.enqueue(instance=build, build_id=build.pk)" in src


def test_build_actions_dispatch_the_job() -> None:
    api_src = _read("netbox_packer/api/views.py")
    ui_src = _read("netbox_packer/views.py")
    assert "dispatch_build(build)" in api_src
    assert "dispatch_build(build)" in ui_src


def test_proxbox_client_targets_template_images_endpoint() -> None:
    src = _read("netbox_packer/proxbox_client.py")
    assert "/cloud/templates/images" in src
    assert '"X-Proxbox-API-Key": proxbox_api_key' in src
    assert '"user_data_yaml": user_data_yaml' in src


def test_migrations_present_for_settings_and_seed() -> None:
    assert (PKG / "migrations" / "0005_packerpluginsettings_proxbox_api.py").is_file()
    seed = _read("netbox_packer/migrations/0006_seed_zabbix_cloud_init.py")
    assert "zabbix-release_latest_7.4+ubuntu26.04_all.deb" in seed
    assert "php8.5-pgsql" in seed
    assert "/usr/share/zabbix/sql-scripts/postgresql/server.sql.gz" in seed
    assert 'installer_type": "cloud_config"' in seed or '"installer_type": "cloud_config"' in seed
    assert '"storage_pool": "local"' in seed


# ── Isolated functional test of the proxbox-api client ────────────────────────


def _load_proxbox_client():
    """Load proxbox_client.py in isolation (it only imports the stdlib)."""
    path = PKG / "proxbox_client.py"
    spec = importlib.util.spec_from_file_location("netbox_packer_proxbox_client_iso", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_call_proxbox_build_posts_cloud_config(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp(b'{"status": "completed", "vmid": 9010}')

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    out = mod.call_proxbox_build(
        proxbox_api_url="http://10.0.30.207:8000/",
        proxbox_api_key="secret-key",
        name="zabbix-7.4-ubuntu-2604-pgsql-nginx",
        vmid=9010,
        target_node="",
        image_url="https://cloud-images.ubuntu.com/releases/26.04/release/img.img",
        user_data_yaml="#cloud-config\nruncmd:\n  - echo hi\n",
        image_storage="local",
        vm_storage="local",
        storage="local",
        snippets_storage="local",
        ssh_host="10.0.30.139",
    )

    assert out == {"status": "completed", "vmid": 9010}
    assert captured["url"] == "http://10.0.30.207:8000/cloud/templates/images"
    assert captured["headers"]["x-proxbox-api-key"] == "secret-key"
    body = captured["body"]
    assert body["user_data_yaml"].startswith("#cloud-config")
    assert body["execute"] is True
    assert body["provider"] == "release_image"
    assert body["ssh_host"] == "10.0.30.139"
    assert body["snippets_storage"] == "local"
    assert body["vmid"] == 9010


def test_call_proxbox_build_raises_on_http_error(monkeypatch) -> None:
    mod = _load_proxbox_client()

    def fake_urlopen(req, timeout=0):
        raise mod.urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {}, fp=__import__("io").BytesIO(b"writes disabled")
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    try:
        mod.call_proxbox_build(
            proxbox_api_url="http://x",
            proxbox_api_key="k",
            name="n",
            vmid=1,
            target_node="",
            image_url="https://x/y.img",
            user_data_yaml="#cloud-config\n",
        )
    except mod.ProxboxApiError as exc:
        assert "403" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected ProxboxApiError")
