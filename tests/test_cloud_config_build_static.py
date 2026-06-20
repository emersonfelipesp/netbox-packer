"""Tests for the cloud-init template image build path.

Static (text/AST) assertions plus an isolated functional test of
``proxbox_client`` — all run without Django or NetBox installed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "netbox_packer"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _literal_assignments(rel: str) -> dict[str, object]:
    tree = ast.parse(_read(rel))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return values


def _resolve_static_value(node: ast.AST, constants: dict[str, object]) -> object:
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    return ast.literal_eval(node)


def _packer_template_seed_defaults(rel: str) -> tuple[str, dict[str, object]]:
    tree = ast.parse(_read(rel))
    constants = _literal_assignments(rel)

    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "get_or_create":
            continue

        name = None
        defaults_node = None
        for keyword in call.keywords:
            if keyword.arg == "name":
                name = _resolve_static_value(keyword.value, constants)
            elif keyword.arg == "defaults":
                defaults_node = keyword.value

        if name != constants.get("TEMPLATE_NAME") or not isinstance(defaults_node, ast.Dict):
            continue

        defaults: dict[str, object] = {}
        for key_node, value_node in zip(defaults_node.keys, defaults_node.values, strict=True):
            if key_node is None:
                continue
            key = ast.literal_eval(key_node)
            try:
                defaults[key] = _resolve_static_value(value_node, constants)
            except ValueError:
                defaults[key] = "<dynamic>"
        if "proxmox_template_id" in defaults:
            return str(name), defaults

    raise AssertionError(f"No PackerTemplate.objects.get_or_create defaults found in {rel}")


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
    # Monitoring agents are injected before the proxbox-api call.
    assert "_inject_monitoring_agents(installer.content, template)" in src
    assert "user_data_yaml=user_data_yaml" in src
    # Gap 1: PackerBuild creation must enqueue the job.
    assert "def dispatch_build(build):" in src
    # PackerBuild is not a jobs-assignable object type in NetBox, so the job must
    # enqueue WITHOUT instance= (it links the build via build_id) — otherwise NetBox
    # raises "Jobs cannot be assigned to this object type" and the UI Build button fails.
    assert "PackerBuildJob.enqueue(build_id=build.pk)" in src
    assert "PackerBuildJob.enqueue(instance=build" not in src


def test_jobs_target_node_unset_becomes_none() -> None:
    # proxbox-api rejects an empty target_node (min_length=1); an unset node must
    # collapse to None, never "".
    src = _read("netbox_packer/jobs.py")
    assert 'target_node = (node or template.proxmox_node or "").strip() or None' in src


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

    influx_seed = _read("netbox_packer/migrations/0007_seed_influxdb_cloud_init.py")
    assert "influxdb-2-ubuntu-2404-proxmox-collector" in influx_seed
    assert "https://repos.influxdata.com/influxdata-archive.key" in influx_seed
    assert "24C975CBA61A024EE1B631787C3D57159FC2F927" in influx_seed
    assert "apt-get install -y influxdb2" in influx_seed
    assert "curl -fsS http://127.0.0.1:8086/health" in influx_seed
    assert "curl -fsS -X POST http://127.0.0.1:8086/api/v2/setup" in influx_seed
    assert "retentionPeriodSeconds" in influx_seed
    assert "INFLUXDB_ADMIN_TOKEN" in influx_seed
    assert '"proxmox_endpoint": PROXMOX_ENDPOINT' in influx_seed
    assert '"proxmox_template_id": TEMPLATE_VMID' in influx_seed


def test_influxdb_seed_targets_development_endpoint_only() -> None:
    constants = _literal_assignments("netbox_packer/migrations/0007_seed_influxdb_cloud_init.py")
    name, defaults = _packer_template_seed_defaults("netbox_packer/migrations/0007_seed_influxdb_cloud_init.py")

    assert constants["CONFIG_NAME"] == "influxdb-2-ubuntu-2404-proxmox-collector"
    assert constants["CONFIG_VERSION"] == "1.0.0"
    assert constants["TEMPLATE_NAME"] == "influxdb-2-ubuntu-2404-proxmox-collector"
    assert constants["TEMPLATE_VMID"] == 9011
    assert constants["PROXMOX_ENDPOINT"] == "https://10.0.30.139:8006"
    assert name == constants["TEMPLATE_NAME"]

    assert defaults["os_family"] == "ubuntu"
    assert defaults["os_version"] == "24.04"
    assert defaults["proxmox_template_id"] == 9011
    assert defaults["proxmox_endpoint"] == "https://10.0.30.139:8006"
    assert defaults["proxmox_node"] == "10.0.30.139"
    assert defaults["storage_pool"] == "local"
    assert defaults["cloud_init_ready"] is True
    assert defaults["build_status"] == "pending"
    assert defaults["proxmox_endpoint"] != "https://10.0.30.9:8006"
    assert defaults["proxmox_node"] != "10.0.30.9"


def test_influxdb_cloud_config_bootstrap_contract() -> None:
    migration = _read("netbox_packer/migrations/0007_seed_influxdb_cloud_init.py")
    assert "packages:" in migration
    assert "qemu-guest-agent" in migration
    assert "https://repos.influxdata.com/influxdata-archive.key" in migration
    assert "24C975CBA61A024EE1B631787C3D57159FC2F927" in migration
    assert "apt-get install -y influxdb2" in migration
    assert "systemctl enable --now qemu-guest-agent" in migration
    assert "systemctl enable --now influxdb" in migration
    assert "curl -fsS http://127.0.0.1:8086/health" in migration
    assert "curl -fsS -X POST http://127.0.0.1:8086/api/v2/setup" in migration
    assert 'ORG="${INFLUXDB_ORG:-nmulticloud}"' in migration
    assert 'BUCKET="${INFLUXDB_BUCKET:-proxmox}"' in migration
    assert 'RETENTION_SECONDS="${INFLUXDB_RETENTION_SECONDS:-2592000}"' in migration
    assert "/etc/nmulticloud/influxdb-collector.env" in migration
    assert "chmod 600 /etc/nmulticloud/influxdb-collector.env" in migration


def test_influxdb_process_is_documented_for_operators_and_agents() -> None:
    required = (
        "influxdb-2-ubuntu-2404-proxmox-collector",
        "9011",
        "https://10.0.30.139:8006",
        "10.0.30.139",
        "10.0.30.9",
    )
    for rel in ("README.md", "CLAUDE.md", "AGENTS.md", "docs/cloud-init-template-images.md", "docs/index.md"):
        doc = _read(rel)
        for text in required:
            assert text in doc, f"{rel} must document {text}"

    mkdocs = _read("mkdocs.yml")
    assert "cloud-init-template-images.md" in mkdocs


def test_powerdns_auth_recursor_seed_contract() -> None:
    rel = "netbox_packer/migrations/0013_seed_powerdns_auth_recursor_cloud_init.py"
    src = _read(rel)
    constants = _literal_assignments(rel)
    name, defaults = _packer_template_seed_defaults(rel)

    assert constants["CONFIG_NAME"] == "powerdns-auth-recursor-ubuntu"
    assert constants["CONFIG_VERSION"] == "1.0.0"
    assert constants["TEMPLATE_NAME"] == "powerdns-auth-recursor-ubuntu"
    assert constants["TEMPLATE_VMID"] == 9019
    assert constants["PROXMOX_ENDPOINT"] == "https://10.0.30.71:8006"
    assert constants["PROXMOX_NODE"] == "10.0.30.71"
    assert name == constants["TEMPLATE_NAME"]

    assert defaults["os_family"] == "ubuntu"
    assert defaults["os_version"] == "24.04"
    assert defaults["proxmox_template_id"] == 9019
    assert defaults["proxmox_endpoint"] == "https://10.0.30.71:8006"
    assert defaults["proxmox_node"] == "10.0.30.71"
    assert defaults["storage_pool"] == "local"
    assert defaults["cloud_init_ready"] is True
    assert defaults["build_status"] == "pending"

    assert '"installer_type": "cloud_config"' in src
    for package in ("pdns-server", "pdns-backend-sqlite3", "pdns-recursor", "qemu-guest-agent"):
        assert package in src

    assert "local-address=127.0.0.1" in src
    assert "local-port=5300" in src
    assert "webserver-address=127.0.0.1" in src
    assert "webserver-port=8081" in src
    assert "webserver-port=8082" in src
    assert "__SET_PDNS_AUTH_API_KEY_AT_PROVISION__" in src
    assert "__SET_PDNS_RECURSOR_API_KEY_AT_PROVISION__" in src
    assert "api-key=changeme" not in src

    assert "local-address=${PRIMARY_IPV4}" in src
    assert "forward-zones=${LOCAL_FORWARD_ZONES}" in src
    assert "PDNS_LOCAL_FORWARD_ZONES:-nmulti.cloud=127.0.0.1:5300" in src
    assert "forward-zones-recurse=%s" in src
    assert "allow-from=${ALLOW_FROM}" in src
    assert "10.0.0.0/8" in src
    assert "172.16.0.0/12" in src
    assert "192.168.0.0/16" in src
    assert "0.0.0.0/0" not in src

    assert "schema.sqlite3.sql" in src
    assert "systemctl enable pdns.service pdns-recursor.service" in src
    assert "systemctl restart pdns.service pdns-recursor.service" in src
    assert "[systemctl, enable, --now, qemu-guest-agent]" in src

    assert "PackerTemplate.objects.filter(name=TEMPLATE_NAME).delete()" in src
    assert "PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()" in src
    assert '"netbox_packer", "0012_seed_powerdns_cloud_init"' in src


def test_powerdns_auth_recursor_process_is_documented_for_operators_and_agents() -> None:
    required = (
        "powerdns-auth-recursor-ubuntu",
        "9019",
        "127.0.0.1:5300",
        "pdns-server",
        "pdns-recursor",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "0.0.0.0/0",
    )
    for rel in ("README.md", "CLAUDE.md", "AGENTS.md", "docs/cloud-init-template-images.md", "docs/index.md"):
        doc = _read(rel)
        for text in required:
            assert text in doc, f"{rel} must document {text}"


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


# ── Monitoring agent injection ────────────────────────────────────────────────


def test_model_has_monitoring_agent_fields() -> None:
    src = _read("netbox_packer/models.py")
    assert "install_qemu_guest_agent = models.BooleanField(" in src
    assert "install_zabbix_agent2 = models.BooleanField(" in src
    assert "zabbix_server = models.CharField(" in src
    assert '"zabbix.nmulti.cloud"' in src


def test_migration_0008_adds_monitoring_agent_fields() -> None:
    src = _read("netbox_packer/migrations/0008_packertemplate_monitoring_agents.py")
    assert '"install_qemu_guest_agent"' in src
    assert '"install_zabbix_agent2"' in src
    assert '"zabbix_server"' in src
    assert '"0007_seed_influxdb_cloud_init"' in src  # correct dependency


def test_jobs_has_monitoring_injection_functions() -> None:
    src = _read("netbox_packer/jobs.py")
    assert "def _zabbix_agent2_bootstrap(zabbix_server" in src
    assert "def _inject_monitoring_agents(user_data_yaml" in src
    # Injection function uses deduplication: skip packages if already present.
    assert '"qemu-guest-agent" not in pkgs' in src
    # Zabbix whole-YAML dedup: skip entirely if zabbix-agent2 already in content.
    assert '"zabbix-agent2" not in user_data_yaml' in src
    # Zabbix bootstrap script uses ServerActive= with the configured server.
    assert "ServerActive=" in src
    # Security: module-level regex guard prevents heredoc break-out via zabbix_server.
    assert "_ZABBIX_SERVER_RE" in src
    assert "raise ValueError" in src


def test_serializer_exposes_monitoring_agent_fields() -> None:
    src = _read("netbox_packer/api/serializers.py")
    assert '"install_qemu_guest_agent"' in src
    assert '"install_zabbix_agent2"' in src
    assert '"zabbix_server"' in src
