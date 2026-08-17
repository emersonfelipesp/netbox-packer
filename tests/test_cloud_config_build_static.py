"""Tests for the cloud-init template image build path.

Static (text/AST) assertions plus an isolated functional test of
``proxbox_client`` — all run without Django or NetBox installed.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import posixpath
import re
import subprocess
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

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


def _logical_shell_lines(script: str) -> list[str]:
    """Join backslash-continued shell lines so a flag check sees a whole command."""

    logical: list[str] = []
    buffer = ""
    for raw in script.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        logical.append((buffer + stripped.strip()) if buffer else stripped)
        buffer = ""
    if buffer:
        logical.append(buffer)
    return logical


def _frozenset_members(rel: str, name: str) -> set[str]:
    """Return the string members of a module-level ``name = frozenset({...})``.

    ``_literal_assignments`` cannot evaluate this, because ``frozenset(...)`` is a
    call rather than a literal. Raises if the assignment is missing, so the guard
    cannot silently degrade into asserting nothing.
    """

    for node in ast.parse(_read(rel)).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if getattr(node.targets[0], "id", None) != name:
            continue
        call = node.value
        assert isinstance(call, ast.Call), f"{name} is not a frozenset(...) call"
        return {member for member in ast.literal_eval(call.args[0]) if isinstance(member, str)}
    raise AssertionError(f"{name} not found in {rel}")


def _packer_template_seed_defaults(rel: str) -> tuple[str, dict[str, object]]:
    tree = ast.parse(_read(rel))
    constants = _literal_assignments(rel)
    dict_assignments = {
        node.targets[0].id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Dict)
    }

    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
            "get_or_create",
            "update_or_create",
        }:
            continue

        name = None
        defaults_node = None
        for keyword in call.keywords:
            if keyword.arg == "name":
                name = _resolve_static_value(keyword.value, constants)
            elif keyword.arg == "defaults":
                defaults_node = keyword.value

        if isinstance(defaults_node, ast.Name):
            defaults_node = dict_assignments.get(defaults_node.id)

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


def _load_jobs_isolated(monkeypatch):
    """Load jobs.py with tiny framework stubs for its pure helper tests."""

    django = ModuleType("django")
    django_conf = ModuleType("django.conf")
    django_conf.settings = SimpleNamespace(PLUGINS_CONFIG={})
    django_utils = ModuleType("django.utils")
    django_utils.timezone = SimpleNamespace(now=lambda: None)
    netbox = ModuleType("netbox")
    netbox_jobs = ModuleType("netbox.jobs")
    netbox_jobs.JobRunner = type("JobRunner", (), {})
    package = ModuleType("netbox_packer")
    package.__path__ = [str(PKG)]
    package_index = ModuleType("netbox_packer.package_index")
    package_index.redact_fileserver_package_token = lambda value, _token: value
    package_index.render_fileserver_package_index = lambda value, **_kwargs: value
    package_index.sanitized_fileserver_package_error = lambda error, _token: error

    for name, module in {
        "django": django,
        "django.conf": django_conf,
        "django.utils": django_utils,
        "netbox": netbox,
        "netbox.jobs": netbox_jobs,
        "netbox_packer": package,
        "netbox_packer.package_index": package_index,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    path = PKG / "jobs.py"
    spec = importlib.util.spec_from_file_location("netbox_packer.jobs_isolated", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_base_image_module():
    spec = importlib.util.spec_from_file_location(
        "netbox_packer.base_image_isolated",
        PKG / "base_image.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Static wiring assertions ──────────────────────────────────────────────────


def test_plugin_settings_has_proxbox_api_fields() -> None:
    src = _read("netbox_packer/models.py")
    assert "proxbox_api_url = models.URLField(" in src
    assert "proxbox_api_key_encrypted = models.CharField(" in src
    assert "def set_proxbox_api_key(self, plain: str) -> None:" in src
    assert "def get_proxbox_api_key(self) -> str:" in src
    assert "def _fernet():" in src


def test_plugin_settings_has_fileserver_package_credentials() -> None:
    src = _read("netbox_packer/models.py")
    assert "fileserver_package_read_user = models.CharField(" in src
    assert "fileserver_package_read_token_encrypted = models.CharField(" in src
    assert "def set_fileserver_package_read_token(self, plain: str) -> None:" in src
    assert "def get_fileserver_package_read_token(self) -> str:" in src

    migration_src = _read("netbox_packer/migrations/0021_packerpluginsettings_fileserver_package_credentials.py")
    assert '("netbox_packer", "0020_seed_influxdb_profiles")' in migration_src
    assert 'name="fileserver_package_read_user"' in migration_src
    assert 'name="fileserver_package_read_token_encrypted"' in migration_src
    assert "editable=False" in migration_src


def test_jobs_branches_on_cloud_config_and_delegates() -> None:
    src = _read("netbox_packer/jobs.py")
    assert 'installer.installer_type == "cloud_config"' in src
    assert "def _run_proxbox_cloud_build(self, build, template, node, timeout):" in src
    assert "from .proxbox_client import ProxboxApiError, call_proxbox_build" in src
    # Monitoring agents are injected before the proxbox-api call.
    assert "_inject_monitoring_agents(installer.content, template)" in src
    assert "fileserver_package_read_token = settings_row.get_fileserver_package_read_token()" in src
    assert "user_data_yaml = render_fileserver_package_index(" in src
    assert "settings_row=settings_row," in src
    assert "template_name=template.name," in src
    assert "is_fileserver_golden_template=template.is_fileserver_golden_template," in src
    assert "redact_fileserver_package_token(str(value), fileserver_package_read_token)" in src
    assert "raise sanitized_fileserver_package_error(exc, fileserver_package_read_token) from None" in src
    assert "user_data_yaml=user_data_yaml" in src
    assert "proxbox-api signed handshake: plan -> preflight -> execute" in src
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
    assert "def _resolve_target_node(template, selected_node, overrides):" in src
    assert "target_node = _resolve_target_node(template, node, build.variable_overrides)" in src


def test_jobs_forwards_endpoint_id_to_proxbox_build() -> None:
    # proxbox-api requires endpoint_id when execute=true (it enforces the
    # allow_writes + access_methods=api_ssh gates). jobs.py must resolve it from
    # variable_overrides and forward it to call_proxbox_build, or every
    # cloud_config bake fails with HTTP 422 "endpoint_id is required".
    src = _read("netbox_packer/jobs.py")
    assert "def _resolve_endpoint_id(overrides):" in src
    assert "endpoint_id = _resolve_endpoint_id(build.variable_overrides)" in src
    assert "endpoint_id=endpoint_id," in src


def test_build_actions_dispatch_the_job() -> None:
    api_src = _read("netbox_packer/api/views.py")
    ui_src = _read("netbox_packer/views.py")
    assert "dispatch_build(build)" in api_src
    assert "dispatch_build(build)" in ui_src


def test_proxbox_client_targets_template_images_endpoint() -> None:
    src = _read("netbox_packer/proxbox_client.py")
    assert "/cloud/templates/images" in src
    assert "/cloud/templates/images/preflight" in src
    assert '"X-Proxbox-API-Key": proxbox_api_key' in src
    assert '"user_data_yaml": user_data_yaml' in src
    assert '"preflight_plan_token": plan_token' in src


def test_migrations_present_for_settings_and_seed() -> None:
    assert (PKG / "migrations" / "0005_packerpluginsettings_proxbox_api.py").is_file()
    seed = _read("netbox_packer/migrations/0006_seed_zabbix_cloud_init.py")
    assert "zabbix-release_latest_7.4+ubuntu26.04_all.deb" in seed
    assert "php8.5-pgsql" in seed
    assert "/usr/share/zabbix/sql-scripts/postgresql/server.sql.gz" in seed
    assert 'installer_type": "cloud_config"' in seed or '"installer_type": "cloud_config"' in seed
    assert '"storage_pool": "local"' in seed

    influx_seed = _read("netbox_packer/migrations/0020_seed_influxdb_profiles.py")
    assert "influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics" in influx_seed
    assert "influxdb-core-3.11.0-ubuntu-2404" in influx_seed
    assert "https://repos.influxdata.com/influxdata-archive.key" in influx_seed
    assert "24C975CBA61A024EE1B631787C3D57159FC2F927" in influx_seed
    assert '"influxdb2=${package_version}"' in influx_seed
    assert '"influxdb3-core=${package_version}"' in influx_seed
    assert "2.9.1|2.9.1[-+~]*" in influx_seed
    assert "3.11.0|3.11.0[-+~]*" in influx_seed
    assert "http://127.0.0.1:8086/health" in influx_seed
    assert "http://127.0.0.1:8181/ready" in influx_seed
    assert "/api/v2/setup" not in influx_seed
    assert "INFLUXDB_ADMIN_TOKEN" not in influx_seed
    assert '"proxmox_endpoint": ""' in influx_seed
    assert '"proxmox_node": "select-at-build"' in influx_seed


def test_historical_influxdb_seed_is_immutable_and_retired_additively() -> None:
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

    additive = _read("netbox_packer/migrations/0020_seed_influxdb_profiles.py")
    assert 'name="influxdb-2-ubuntu-2404-proxmox-collector"' in additive
    assert "legacy.content = INFLUXDB_OSS2_CLOUD_CONFIG" in additive
    assert 'build_status="pending"' in additive


def test_influxdb_cloud_config_bootstrap_contract() -> None:
    migration = _read("netbox_packer/migrations/0020_seed_influxdb_profiles.py")
    assert "packages:" in migration
    assert "https://repos.influxdata.com/influxdata-archive.key" in migration
    assert "24C975CBA61A024EE1B631787C3D57159FC2F927" in migration
    assert "apt-mark hold influxdb2" in migration
    assert "apt-mark hold influxdb3-core" in migration
    assert "systemctl enable --now influxdb.service" in migration
    assert "systemctl enable --now influxdb3-core.service" in migration
    assert "Credentials and initial setup are intentionally deferred to typed NMS RPC" in migration
    for forbidden in (
        "/api/v2/setup",
        "password:",
        "operator_token",
        "INFLUXDB_ADMIN_TOKEN",
        "openssl rand",
    ):
        assert forbidden not in migration


def test_influxdb_profile_cloud_configs_parse_and_catalog_matches_seed() -> None:
    rel = "netbox_packer/migrations/0020_seed_influxdb_profiles.py"
    constants = _literal_assignments(rel)
    for key in ("INFLUXDB_OSS2_CLOUD_CONFIG", "INFLUXDB_CORE3_CLOUD_CONFIG"):
        content = constants[key]
        assert isinstance(content, str)
        parsed = yaml.safe_load(content)
        assert parsed["package_update"] is False
        assert parsed["package_upgrade"] is False
        assert parsed["write_files"][0]["content"].startswith("#!/usr/bin/env bash")

    spec = importlib.util.spec_from_file_location(
        "influxdb_profiles",
        PKG / "influxdb_profiles.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migration = _read(rel)
    assert {profile["family"] for profile in module.INFLUXDB_PROFILES} == {"oss2", "core3"}
    for profile in module.INFLUXDB_PROFILES:
        assert profile["template_name"] in migration
        assert profile["installer_config_name"] in migration
        assert str(profile["default_vmid"]) in migration


def test_influxdb_process_is_documented_for_operators_and_agents() -> None:
    required = (
        "2.9.1",
        "3.11.0",
        "9050",
        "9051",
        "endpoint_id",
        "target_node",
        "nms-secret",
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


def test_fileserver_allinone_seed_contract() -> None:
    rel = "netbox_packer/migrations/0014_seed_fileserver_allinone_cloud_init.py"
    seed_rel = "netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml"
    src = _read(rel)
    seed = _read(seed_rel)
    constants = _literal_assignments(rel)
    name, defaults = _packer_template_seed_defaults(rel)

    assert yaml.safe_load(seed)["package_update"] is True
    assert seed.startswith("#cloud-config\n")

    assert constants["CONFIG_NAME"] == "fileserver-allinone-cloud-config"
    assert constants["CONFIG_VERSION"] == "1.0.0"
    assert constants["TEMPLATE_NAME"] == "tpl-fileserver-allinone-ubuntu-2404"
    assert constants["PROXMOX_ENDPOINT"] == "https://10.0.30.71:8006"
    assert constants["PROXMOX_NODE"] == "10.0.30.71"
    assert name == constants["TEMPLATE_NAME"]

    assert defaults["os_family"] == "ubuntu"
    assert defaults["os_version"] == "24.04"
    assert defaults["proxmox_endpoint"] == "https://10.0.30.71:8006"
    assert defaults["proxmox_node"] == "10.0.30.71"
    assert defaults["storage_pool"] == "local"
    assert defaults["cloud_init_ready"] is True
    assert defaults["build_status"] == "pending"
    assert defaults["install_qemu_guest_agent"] is True
    assert defaults["install_zabbix_agent2"] is True
    assert defaults["zabbix_server"] == "zabbix.nmulti.cloud"

    assert '"installer_type": "cloud_config"' in src
    for package in (
        "samba",
        "samba-dsdb-modules",
        "samba-vfs-modules",
        "winbind",
        "libnss-winbind",
        "libpam-winbind",
        "krb5-user",
        "acl",
        "attr",
        "chrony",
        "nginx",
        "php-fpm",
        "php-ldap",
        "php-smbclient",
        "php-pgsql",
        "php-gd",
        "php-curl",
        "php-zip",
        "php-xml",
        "php-mbstring",
        "php-intl",
        "php-bcmath",
        "php-gmp",
        "php-imagick",
        "smbclient",
        "cifs-utils",
        "postgresql-client",
        "python3-venv",
        "qemu-guest-agent",
        "zabbix-agent2",
    ):
        assert package in seed

    assert "apt-get install -y zabbix-agent2" in seed
    assert "apt-get install -y zabbix-agent2 nms-fileserver-agent" not in seed
    assert 'python3 -m venv "${NMS_FILESERVER_AGENT_VENV_DIR}"' in seed
    assert "pip install --no-deps" in seed
    assert '"${NMS_FILESERVER_AGENT_PIP_SPEC}"' in seed
    assert 'NMS_FILESERVER_AGENT_PIP_SPEC="${NMS_FILESERVER_AGENT_PIP_SPEC:-nms-fileserver-agent==0.1.0}"' in seed
    config = yaml.safe_load(seed)
    pip_config = next(item for item in config["write_files"] if item["path"] == "/etc/nms-fileserver-agent/pip.conf")
    assert pip_config["permissions"] == "0600"
    assert (
        "https://__NMS_FILESERVER_PACKAGE_READ_USER__:__NMS_FILESERVER_PACKAGE_READ_TOKEN__@" in pip_config["content"]
    )
    assert "git.nmulti.cloud/api/packages/N-MultiCloud/pypi/simple/" in pip_config["content"]
    assert "extra-index-url =" in pip_config["content"]
    assert "NMS_FILESERVER_PACKAGE_READ_TOKEN" in pip_config["content"]
    assert "service environment" not in pip_config["content"]
    assert "Operators rotate NMS_FILESERVER_PACKAGE_READ_TOKEN" not in pip_config["content"]
    assert "PackerPluginSettings" in pip_config["content"]
    assert "set_fileserver_package_read_token()" in pip_config["content"]
    assert "dedicated non-human Gitea package-Read token" in pip_config["content"]
    assert 'PIP_INDEX_URL="https://pypi.org/simple" PIP_EXTRA_INDEX_URL=""' in seed
    assert 'pip install "httpx>=0.27"' in seed
    assert 'PIP_CONFIG_FILE="${NMS_FILESERVER_AGENT_DIR}/pip.conf"' in seed
    assert 'env -u PIP_INDEX_URL PIP_EXTRA_INDEX_URL=""' in seed
    assert "--extra-index-url" not in seed
    assert "systemctl enable nms-fileserver-agent-enroll.service" in seed
    assert "systemctl enable --now nms-fileserver-agent-heartbeat.timer" in seed
    assert "systemctl disable --now nms-fileserver-agent-enroll.service || true" in seed
    assert "systemctl disable --now nms-fileserver-agent-heartbeat.timer || true" in seed
    assert "systemctl disable --now nms-fileserver-agent-heartbeat.service || true" in seed
    assert "systemctl disable --now nms-fileserver-agent || true" not in seed
    assert "NMS_BACKEND_URL=https://backend.nms.nmulti.cloud" in seed
    assert "NETBOX_URL=https://netbox.nmulti.cloud" in seed
    assert "NMS_FILESERVER_ENROLLMENT_TOKEN=" not in seed
    assert "Server=zabbix.nmulti.cloud" in seed
    assert "systemctl disable --now nginx || true" in seed
    assert "systemctl mask smbd nmbd winbind || true" in seed
    assert "samba-tool domain provision" not in seed
    assert "occ maintenance:install" not in seed
    assert "PackerTemplate.objects.filter(name=TEMPLATE_NAME).delete()" in src
    assert "PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()" in src
    assert '"netbox_packer", "0013_seed_powerdns_auth_recursor_cloud_init"' in src


def test_fileserver_allinone_process_is_documented_for_operators_and_agents() -> None:
    required = (
        "tpl-fileserver-allinone-ubuntu-2404",
        "fileserver-allinone-cloud-config",
        "9300",
        "https://10.0.30.71:8006",
        "10.0.30.71",
        "nms-fileserver-agent",
        "NMS_FILESERVER_AGENT_PIP_SPEC",
        "PackerPluginSettings",
        "fileserver_package_read_user",
        "set_fileserver_package_read_token",
        "package-Read",
        "python3-venv",
        "nms-fileserver-agent-enroll.service",
        "nms-fileserver-agent-heartbeat.timer",
        "https://backend.nms.nmulti.cloud",
        "https://netbox.nmulti.cloud",
    )
    for rel in ("README.md", "CLAUDE.md", "AGENTS.md", "docs/cloud-init-template-images.md", "docs/index.md"):
        doc = _read(rel)
        for text in required:
            assert text in doc, f"{rel} must document {text}"
        assert "NMS_FILESERVER_PACKAGE_READ_USER" not in doc
        assert "NMS_FILESERVER_PACKAGE_READ_TOKEN" not in doc


def test_fileserver_package_index_upgrade_migration_contract() -> None:
    rel = "netbox_packer/migrations/0017_update_fileserver_agent_package_index.py"
    src = _read(rel)
    constants = _literal_assignments(rel)

    assert constants["CONFIG_NAME"] == "fileserver-allinone-cloud-config"
    assert constants["PREVIOUS_CONFIG_VERSION"] == "1.0.0"
    assert constants["CONFIG_VERSION"] == "1.0.1"
    assert constants["TEMPLATE_NAME"] == "tpl-fileserver-allinone-ubuntu-2404"
    assert constants["TEMPLATE_VMID"] == 9300
    assert "service environment" in constants["FILESERVER_ALLINONE_CLOUD_CONFIG"]
    assert "Operators rotate NMS_FILESERVER_PACKAGE_READ_TOKEN" in constants["FILESERVER_ALLINONE_CLOUD_CONFIG"]
    assert "PackerInstallerConfig.objects.update_or_create(" in src
    assert "proxmox_template_id=TEMPLATE_VMID" in src
    # The template's own description must be corrected alongside proxmox_template_id —
    # otherwise it keeps advertising the superseded VMID 9032 after this migration runs.
    assert "VMID 9300" in src
    assert "VMID 9032" not in src.split("def update_fileserver_package_index", 1)[1].split("def ", 1)[0]
    assert 'update(installer_config=previous, build_status="pending")' in src
    assert "PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()" in src
    assert '("netbox_packer", "0016_seed_ubuntu_lts_base_cloud_init")' in src


def test_fileserver_package_settings_comment_migration_contract() -> None:
    rel = "netbox_packer/migrations/0022_update_fileserver_package_settings_comment.py"
    src = _read(rel)
    constants = _literal_assignments(rel)
    seed = _read("netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml")

    assert constants["CONFIG_NAME"] == "fileserver-allinone-cloud-config"
    assert constants["CONFIG_VERSION"] == "1.0.1"
    historical_content = _literal_assignments("netbox_packer/migrations/0017_update_fileserver_agent_package_index.py")[
        "FILESERVER_ALLINONE_CLOUD_CONFIG"
    ]
    assert constants["STALE_PIP_CONF_COMMENT"] in historical_content
    assert constants["STALE_PIP_CONF_COMMENT"] not in seed
    assert constants["SETTINGS_PIP_CONF_COMMENT"] in seed
    assert (
        historical_content.replace(
            constants["STALE_PIP_CONF_COMMENT"],
            constants["SETTINGS_PIP_CONF_COMMENT"],
            1,
        )
        == seed
    )
    assert "updated_content = config.content.replace(" in src
    assert "checksum=hashlib.sha256(updated_content.encode()).hexdigest()" in src
    assert 'update(build_status="pending")' in src
    assert '"0021_packerpluginsettings_fileserver_package_credentials"' in src
    assert "migrations.RunPython(update_fileserver_package_settings_comment, migrations.RunPython.noop)" in src


def test_packertemplate_name_is_unique_at_the_db_level() -> None:
    """Historical/defense-in-depth: `name` uniqueness, not the current credential boundary.

    At the time migration 0018 landed, the File Server credential guard
    trusted `PackerTemplate.name` alone, so without a DB-level uniqueness
    constraint a differently-owned template could be renamed to
    `FILESERVER_TEMPLATE_NAME` and pass `render_fileserver_package_index`'s
    identity check, exfiltrating the package-read credential. Migration 0019
    replaced `name` with the immutable `is_fileserver_golden_template` flag as
    the actual trust boundary; this constraint remains as defense in depth
    against two rows sharing the trusted name simultaneously.
    """
    models_src = _read("netbox_packer/models.py")
    tree = ast.parse(models_src)
    packer_template = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PackerTemplate"
    )
    name_assign = next(
        node
        for node in packer_template.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and node.targets[0].id == "name"
    )
    field_call = name_assign.value
    assert isinstance(field_call, ast.Call)
    unique_kwarg = next((kw for kw in field_call.keywords if kw.arg == "unique"), None)
    assert unique_kwarg is not None, "PackerTemplate.name must declare unique=True"
    assert ast.literal_eval(unique_kwarg.value) is True

    migration_rel = "netbox_packer/migrations/0018_alter_packertemplate_name_unique.py"
    migration_src = _read(migration_rel)
    assert '("netbox_packer", "0017_update_fileserver_agent_package_index")' in migration_src
    assert 'model_name="packertemplate"' in migration_src
    assert 'name="name"' in migration_src
    assert "unique=True" in migration_src


def test_packertemplate_has_immutable_golden_template_flag() -> None:
    """`unique=True` on `name` (migration 0018) stops two rows sharing the trusted
    name *simultaneously*, but not the trusted row being renamed away and a
    different row later reclaiming the freed name. `is_fileserver_golden_template`
    must be `editable=False` so it can only be set by a migration, never through
    `PackerTemplateForm` or the DRF serializer.
    """
    models_src = _read("netbox_packer/models.py")
    tree = ast.parse(models_src)
    packer_template = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PackerTemplate"
    )
    flag_assign = next(
        node
        for node in packer_template.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and node.targets[0].id == "is_fileserver_golden_template"
    )
    field_call = flag_assign.value
    assert isinstance(field_call, ast.Call)
    kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in field_call.keywords}
    assert kwargs.get("editable") is False, "is_fileserver_golden_template must declare editable=False"
    assert kwargs.get("default") is False

    forms_src = _read("netbox_packer/forms.py")
    form_tree = ast.parse(forms_src)
    packer_template_form = next(
        node for node in form_tree.body if isinstance(node, ast.ClassDef) and node.name == "PackerTemplateForm"
    )
    meta = next(node for node in packer_template_form.body if isinstance(node, ast.ClassDef) and node.name == "Meta")
    fields_assign = next(node for node in meta.body if isinstance(node, ast.Assign) and node.targets[0].id == "fields")
    form_fields = ast.literal_eval(fields_assign.value)
    assert "is_fileserver_golden_template" not in form_fields

    serializers_src = _read("netbox_packer/api/serializers.py")
    assert (
        "is_fileserver_golden_template"
        not in serializers_src.split("class PackerTemplateSerializer", 1)[1].split("class ", 1)[0]
    )

    migration_rel = "netbox_packer/migrations/0019_stamp_fileserver_golden_template.py"
    migration_src = _read(migration_rel)
    assert '("netbox_packer", "0018_alter_packertemplate_name_unique")' in migration_src
    assert 'name="is_fileserver_golden_template"' in migration_src
    assert "editable=False" in migration_src
    assert "migrations.RunPython(stamp_golden_template, unstamp_golden_template)" in migration_src
    assert 'TEMPLATE_NAME = "tpl-fileserver-allinone-ubuntu-2404"' in migration_src


class _FakePackerPluginSettings:
    def __init__(self, user: str = "", token: str = "") -> None:
        self.fileserver_package_read_user = user
        self._fileserver_package_read_token = token

    def get_fileserver_package_read_token(self) -> str:
        return self._fileserver_package_read_token


def test_fileserver_package_index_authorizes_by_flag_not_name() -> None:
    """Simulate the rename-then-reclaim bypass the flag exists to close.

    `render_fileserver_package_index` must authorize on
    `is_fileserver_golden_template` alone, independent of `template_name` — so a
    row that still carries the trusted name after being reclaimed (flag False)
    is rejected, and the real golden row keeps working even if renamed away from
    `FILESERVER_TEMPLATE_NAME` (flag True survives renames).
    """
    mod = _load_package_index()
    config = (
        "index-url = https://"
        + mod.PACKAGE_READ_USER_PLACEHOLDER
        + ":"
        + mod.PACKAGE_READ_TOKEN_PLACEHOLDER
        + "@git.nmulti.cloud/api/packages/N-MultiCloud/pypi/simple/"
    )

    # A new row that reclaimed the trusted name after the original was renamed
    # away must NOT receive credentials merely because the name matches.
    try:
        mod.render_fileserver_package_index(
            config,
            settings_row=_FakePackerPluginSettings("fileserver reader", "token"),
            template_name=mod.FILESERVER_TEMPLATE_NAME,
            is_fileserver_golden_template=False,
        )
    except RuntimeError as exc:
        assert "not the File Server golden template" in str(exc)
    else:  # pragma: no cover - name alone must never authorize credential injection
        raise AssertionError("expected a reclaimed name with flag=False to be rejected")

    # The original golden template, renamed away, must keep working because the
    # flag (not the name) is what carries trust.
    rendered = mod.render_fileserver_package_index(
        config,
        settings_row=_FakePackerPluginSettings("fileserver reader", "token"),
        template_name="renamed-golden-template",
        is_fileserver_golden_template=True,
    )
    assert mod.PACKAGE_READ_USER_PLACEHOLDER not in rendered
    assert mod.PACKAGE_READ_TOKEN_PLACEHOLDER not in rendered


def _load_package_index():
    """Load package_index.py in isolation (it only imports the stdlib)."""
    path = PKG / "package_index.py"
    spec = importlib.util.spec_from_file_location("netbox_packer_package_index_iso", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fileserver_package_index_credentials_are_required_and_url_encoded() -> None:
    mod = _load_package_index()
    config = (
        "index-url = https://"
        + mod.PACKAGE_READ_USER_PLACEHOLDER
        + ":"
        + mod.PACKAGE_READ_TOKEN_PLACEHOLDER
        + "@git.nmulti.cloud/api/packages/N-MultiCloud/pypi/simple/"
    )

    try:
        mod.render_fileserver_package_index(
            config,
            settings_row=_FakePackerPluginSettings(),
            template_name=mod.FILESERVER_TEMPLATE_NAME,
            is_fileserver_golden_template=True,
        )
    except RuntimeError as exc:
        assert "PackerPluginSettings.fileserver_package_read_user" in str(exc)
        assert "PackerPluginSettings.fileserver_package_read_token" in str(exc)
    else:  # pragma: no cover - a credentialed bake must fail closed
        raise AssertionError("expected missing package-index credentials to fail")

    rendered = mod.render_fileserver_package_index(
        config,
        settings_row=_FakePackerPluginSettings("fileserver reader", "read/token?only"),
        template_name=mod.FILESERVER_TEMPLATE_NAME,
        is_fileserver_golden_template=True,
    )
    assert "fileserver%20reader:read%2Ftoken%3Fonly@" in rendered
    assert "read/token?only" not in rendered


def test_fileserver_package_index_rejects_placeholders_on_other_templates() -> None:
    """An unrelated template embedding the placeholder strings must never receive credentials."""
    mod = _load_package_index()
    config = (
        "index-url = https://"
        + mod.PACKAGE_READ_USER_PLACEHOLDER
        + ":"
        + mod.PACKAGE_READ_TOKEN_PLACEHOLDER
        + "@git.nmulti.cloud/api/packages/N-MultiCloud/pypi/simple/"
    )
    try:
        mod.render_fileserver_package_index(
            config,
            settings_row=_FakePackerPluginSettings("fileserver reader", "read/token?only"),
            template_name="some-other-template",
            is_fileserver_golden_template=False,
        )
    except RuntimeError as exc:
        assert "not the File Server golden template" in str(exc)
    else:  # pragma: no cover - credential injection must be scoped to the File Server template
        raise AssertionError("expected placeholders on an unrelated template to be rejected")


def test_fileserver_package_index_non_target_passthrough_and_log_redaction() -> None:
    mod = _load_package_index()
    unrelated = "#cloud-config\npackages:\n  - qemu-guest-agent\n"
    assert (
        mod.render_fileserver_package_index(
            unrelated,
            settings_row=_FakePackerPluginSettings(),
            template_name="some-other-template",
            is_fileserver_golden_template=False,
        )
        == unrelated
    )
    assert (
        mod.render_fileserver_package_index(
            unrelated,
            settings_row=_FakePackerPluginSettings(),
            template_name=mod.FILESERVER_TEMPLATE_NAME,
            is_fileserver_golden_template=True,
        )
        == unrelated
    )

    package_token = "read/token?only"
    output = "raw=read/token?only encoded=read%2Ftoken%3Fonly"
    redacted = mod.redact_fileserver_package_token(output, package_token)
    assert "read/token?only" not in redacted
    assert "read%2Ftoken%3Fonly" not in redacted
    assert redacted.count(mod.REDACTED_PACKAGE_TOKEN) == 2

    try:
        raise RuntimeError(output)
    except RuntimeError as original:
        try:
            raise mod.sanitized_fileserver_package_error(original, package_token) from None
        except RuntimeError as safe_error:
            formatted = "".join(traceback.format_exception(safe_error))
    assert "read/token?only" not in formatted
    assert "read%2Ftoken%3Fonly" not in formatted
    assert formatted.count(mod.REDACTED_PACKAGE_TOKEN) == 2


def test_fileserver_package_index_target_without_placeholders_fails_closed() -> None:
    mod = _load_package_index()
    target_without_placeholders = f"#cloud-config\npath: {mod.FILESERVER_PIP_CONFIG_PATH}\n"
    try:
        mod.render_fileserver_package_index(
            target_without_placeholders,
            settings_row=_FakePackerPluginSettings(),
            template_name=mod.FILESERVER_TEMPLATE_NAME,
            is_fileserver_golden_template=True,
        )
    except RuntimeError as exc:
        assert "placeholders are missing" in str(exc)
    else:  # pragma: no cover - the target config must never fall back to public PyPI
        raise AssertionError("expected missing File Server placeholders to fail")


def test_fileserver_package_index_has_no_service_environment_dependency() -> None:
    src = _read("netbox_packer/package_index.py")
    assert "import os" not in src
    assert "os.environ" not in src
    assert "PACKAGE_READ_USER_ENV" not in src
    assert "PACKAGE_READ_TOKEN_ENV" not in src


def test_passbolt_ce_seed_contract() -> None:
    rel = "netbox_packer/migrations/0015_seed_passbolt_cloud_init.py"
    seed_rel = "netbox_packer/seeds/passbolt-ce-ubuntu-2404.cloud-config.yaml"
    src = _read(rel)
    seed = _read(seed_rel)
    constants = _literal_assignments(rel)
    name, defaults = _packer_template_seed_defaults(rel)

    assert constants["PASSBOLT_CLOUD_CONFIG"] == seed
    assert yaml.safe_load(seed)["package_update"] is True
    assert seed.startswith("#cloud-config\n")

    assert constants["CONFIG_NAME"] == "passbolt-ce-ubuntu-2404"
    assert constants["CONFIG_VERSION"] == "1.0.0"
    assert constants["TEMPLATE_NAME"] == "passbolt-ce-ubuntu-2404"
    assert constants["TEMPLATE_VMID"] == 9060
    assert constants["PROXMOX_ENDPOINT"] == "https://10.0.30.71:8006"
    assert constants["PROXMOX_NODE"] == "10.0.30.71"
    assert name == constants["TEMPLATE_NAME"]

    assert defaults["os_family"] == "ubuntu"
    assert defaults["os_version"] == "24.04"
    assert defaults["proxmox_template_id"] == 9060
    assert defaults["proxmox_endpoint"] == "https://10.0.30.71:8006"
    assert defaults["proxmox_node"] == "10.0.30.71"
    assert defaults["storage_pool"] == "local"
    assert defaults["cloud_init_ready"] is True
    assert defaults["build_status"] == "pending"
    assert defaults["install_qemu_guest_agent"] is True
    assert defaults["install_zabbix_agent2"] is True
    assert defaults["zabbix_server"] == "zabbix.nmulti.cloud"

    assert '"installer_type": "cloud_config"' in src

    # Native Passbolt CE install via the official checksum-pinned repo setup.
    assert "https://download.passbolt.com/ce/installer/passbolt-repo-setup.ce.sh" in seed
    assert "passbolt-ce-SHA512SUM.txt" in seed
    assert "sha512sum -c passbolt-ce-SHA512SUM.txt" in seed
    assert "apt-get install -y passbolt-ce-server" in seed
    assert "mariadb-server" in seed

    # Reverse-proxy contract: plain HTTP, TLS terminated upstream (no SSL here).
    assert "passbolt/nginx-configuration-three-choices select none" in seed
    assert "passbolt/nginx-domain string credential.nmulti.cloud" in seed
    assert "https://credential.nmulti.cloud" in seed
    # JWT + base URL are injected as php-fpm pool env[] entries (effective at
    # runtime despite clear_env), not an inert env drop-in.
    assert "env[APP_FULL_BASE_URL]" in seed
    assert "PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED" in seed
    assert "create_jwt_keys" in seed

    # No baked secret: the local DB password is generated on first boot.
    assert "openssl rand -hex 24" in seed
    assert "/etc/passbolt/.db_password" in seed

    # Monitoring agents must be injected at build time, not present in the seed.
    assert "zabbix-agent2" not in seed
    assert "qemu-guest-agent" not in seed

    # SMTP intentionally deferred.
    assert "SMTP is intentionally unconfigured" in seed

    # Installer config content is authoritative (self-healing) via update_or_create,
    # so a stale row from an earlier seed iteration is refreshed, not left behind.
    assert "PackerInstallerConfig.objects.update_or_create(" in src

    # Reversible seed + correct dependency chain.
    assert "PackerTemplate.objects.filter(name=TEMPLATE_NAME).delete()" in src
    assert "PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()" in src
    assert '"netbox_packer", "0014_seed_fileserver_allinone_cloud_init"' in src


def test_passbolt_ce_process_is_documented_for_operators_and_agents() -> None:
    required = (
        "passbolt-ce-ubuntu-2404",
        "9060",
        "https://10.0.30.71:8006",
        "10.0.30.71",
        "credential.nmulti.cloud",
        "passbolt-ce-server",
        "PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED",
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


_RECIPE_DIGEST = "a" * 64
_PLAN_TOKEN = "signed-plan-token-" + ("b" * 64)


def _proxbox_build_kwargs() -> dict:
    return {
        "proxbox_api_url": "http://10.0.30.207:8000/",
        "proxbox_api_key": "secret-key",
        "name": "zabbix-7.4-ubuntu-2604-pgsql-nginx",
        "vmid": 9010,
        "target_node": "pve-dev-1",
        "image_url": "https://cloud-images.ubuntu.com/releases/26.04/release/img.img",
        "user_data_yaml": "#cloud-config\nruncmd:\n  - echo hi\n",
        "endpoint_id": 17,
        "image_storage": "local",
        "vm_storage": "local",
        "storage": "local",
        "snippets_storage": "local",
        "ssh_host": "10.0.30.139",
    }


def _plan_response() -> dict:
    return {
        "contract_version": "2.0",
        "status": "planned",
        "vmid": 9010,
        "recipe_digest": _RECIPE_DIGEST,
    }


def _preflight_response(**overrides) -> dict:
    response = {
        "contract_version": "1.0",
        "ready": True,
        "writes_enabled": True,
        "recipe_digest": _RECIPE_DIGEST,
        "plan_token": _PLAN_TOKEN,
        "expires_at": 4_000_000_000.0,
        "capabilities": [],
        "findings": [],
    }
    response.update(overrides)
    return response


def _executed_response(**overrides) -> dict:
    response = {
        "contract_version": "2.0",
        "status": "completed",
        "vmid": 9010,
        "template_vmid": 9010,
        "operation_id": "operation-1",
        "verified": True,
        "execution_enabled": True,
        "execution": {
            "attempted": True,
            "enabled": True,
            "exit_code": 0,
        },
        "diagnostics": [],
    }
    response.update(overrides)
    return response


def _install_proxbox_responses(monkeypatch, mod, responses: list[dict | BaseException]) -> list[dict]:
    captured: list[dict] = []

    def fake_urlopen(req, timeout=0):
        captured.append(
            {
                "url": req.full_url,
                "headers": {key.lower(): value for key, value in req.header_items()},
                "body": json.loads(req.data.decode()),
                "timeout": timeout,
            }
        )
        response = responses[len(captured) - 1]
        if isinstance(response, BaseException):
            raise response
        return _FakeResp(json.dumps(response).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_call_proxbox_build_uses_signed_preflight_sequence(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [_plan_response(), _preflight_response(), _executed_response()],
    )

    out = mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert out == _executed_response()
    assert [call["url"] for call in captured] == [
        "http://10.0.30.207:8000/cloud/templates/images",
        "http://10.0.30.207:8000/cloud/templates/images/preflight",
        "http://10.0.30.207:8000/cloud/templates/images",
    ]
    assert all(call["headers"]["x-proxbox-api-key"] == "secret-key" for call in captured)

    plan_body, preflight_body, execute_body = [call["body"] for call in captured]
    assert plan_body["execute"] is False
    assert plan_body["user_data_yaml"].startswith("#cloud-config")
    assert preflight_body == {
        "contract_version": "1.0",
        "endpoint_id": 17,
        "target_node": "pve-dev-1",
        "vmid": 9010,
        "provider": "release_image",
        "image_storage": "local",
        "vm_storage": "local",
        "snippets_storage": "local",
        "recipe_digest": _RECIPE_DIGEST,
        "snippets_required": True,
    }
    assert execute_body["execute"] is True
    assert execute_body["preflight_plan_token"] == _PLAN_TOKEN
    assert set(execute_body) == set(plan_body) | {"preflight_plan_token"}
    for key in set(plan_body) - {"execute"}:
        assert execute_body[key] == plan_body[key], f"build field drifted between plan and execute: {key}"


def test_call_proxbox_build_fails_when_preflight_is_unreachable(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [_plan_response(), mod.urllib.error.URLError("connection refused")],
    )

    with pytest.raises(mod.ProxboxApiError, match="signed preflight failed.*unreachable"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 2


def test_call_proxbox_build_surfaces_not_ready_findings(monkeypatch) -> None:
    mod = _load_proxbox_client()
    finding = {
        "code": "vmid_unavailable",
        "severity": "error",
        "target": "vmid:9010",
        "message": "VMID 9010 is already allocated.",
    }
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [_plan_response(), _preflight_response(ready=False, plan_token=None, findings=[finding])],
    )

    with pytest.raises(mod.ProxboxApiError, match="vmid_unavailable.*already allocated"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 2


def test_call_proxbox_build_fails_when_preflight_returns_no_plan_token(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [_plan_response(), _preflight_response(plan_token=None)],
    )

    with pytest.raises(mod.ProxboxApiError, match="ready=true but no plan_token"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 2


def test_call_proxbox_build_fails_when_preflight_writes_are_disabled(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [_plan_response(), _preflight_response(writes_enabled=False)],
    )

    with pytest.raises(mod.ProxboxApiError, match="writes_enabled=false"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 2


def test_call_proxbox_build_fails_expired_plan_before_execute(monkeypatch) -> None:
    mod = _load_proxbox_client()
    monkeypatch.setattr(mod.time, "time", lambda: 1_000.0)
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [_plan_response(), _preflight_response(expires_at=999.0)],
    )

    with pytest.raises(mod.ProxboxApiError, match="plan expired before execution"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 2


def test_call_proxbox_build_rejects_older_api_without_preflight(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured: list[dict] = []

    def fake_urlopen(req, timeout=0):
        captured.append(json.loads(req.data.decode()))
        if len(captured) == 1:
            return _FakeResp(json.dumps(_plan_response()).encode())
        raise mod.urllib.error.HTTPError(
            req.full_url,
            404,
            "Not Found",
            {},
            fp=__import__("io").BytesIO(b'{"detail":"Not Found"}'),
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(mod.ProxboxApiError, match="incompatible proxbox-api.*legacy one-step"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 2


@pytest.mark.parametrize(
    ("error_code", "expected"),
    (
        ("preflight_plan_expired", "expired before execution"),
        ("preflight_plan_mismatch", "did not match the server-rendered plan"),
    ),
)
def test_call_proxbox_build_surfaces_execute_plan_rejection(
    monkeypatch,
    error_code,
    expected,
) -> None:
    mod = _load_proxbox_client()
    captured: list[dict] = []

    def fake_urlopen(req, timeout=0):
        captured.append(json.loads(req.data.decode()))
        if len(captured) == 1:
            return _FakeResp(json.dumps(_plan_response()).encode())
        if len(captured) == 2:
            return _FakeResp(json.dumps(_preflight_response()).encode())
        body = json.dumps({"detail": {"code": error_code, "message": "rejected"}}).encode()
        raise mod.urllib.error.HTTPError(
            req.full_url,
            409,
            "Conflict",
            {},
            fp=__import__("io").BytesIO(body),
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(mod.ProxboxApiError, match=expected):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 3
    assert captured[-1]["preflight_plan_token"] == _PLAN_TOKEN


def test_call_proxbox_build_rejects_unverified_execute_response(monkeypatch) -> None:
    mod = _load_proxbox_client()
    captured = _install_proxbox_responses(
        monkeypatch,
        mod,
        [
            _plan_response(),
            _preflight_response(),
            _executed_response(
                status="recovery_required",
                verified=False,
                diagnostics=[
                    {
                        "code": "artifact_verification_failed",
                        "severity": "error",
                        "target": "vmid:9010",
                        "message": "Preserve the partial artifact for recovery.",
                    }
                ],
            ),
        ],
    )

    with pytest.raises(mod.ProxboxApiError, match="did not confirm.*artifact_verification_failed"):
        mod.call_proxbox_build(**_proxbox_build_kwargs())

    assert len(captured) == 3


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
            vmid=100,
            target_node="pve-1",
            image_url="https://x/y.img",
            user_data_yaml="#cloud-config\n",
            endpoint_id=1,
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
    assert "install_nms_agent = models.BooleanField(" in src
    assert "nms_agent_backend_url = models.URLField(" in src
    assert 'schemes=["https"]' in src
    assert "validators=[NMS_AGENT_BACKEND_URL_VALIDATOR]" in src
    assert "zabbix_server = models.CharField(" in src
    assert '"zabbix.nmulti.cloud"' in src
    assert 'default="https://backend.nms.nmulti.cloud"' in src
    assert "provisions_service = models.CharField(" in src
    assert 'default="", editable=False' in src

    forms_src = _read("netbox_packer/forms.py")
    serializers_src = _read("netbox_packer/api/serializers.py")
    assert "def clean_nms_agent_backend_url(self):" in forms_src
    assert "NMS_AGENT_BACKEND_URL_VALIDATOR(value)" in forms_src
    assert "def validate_nms_agent_backend_url(self, value):" in serializers_src
    assert 'urlsplit(value).scheme.lower() != "https"' in serializers_src


def test_migration_0008_adds_monitoring_agent_fields() -> None:
    src = _read("netbox_packer/migrations/0008_packertemplate_monitoring_agents.py")
    assert '"install_qemu_guest_agent"' in src
    assert '"install_zabbix_agent2"' in src
    assert '"zabbix_server"' in src
    assert '"0007_seed_influxdb_cloud_init"' in src  # correct dependency


def test_migration_0023_adds_optional_nms_agent_and_service_marker() -> None:
    src = _read("netbox_packer/migrations/0023_packertemplate_nms_agent_and_service_marker.py")
    assert '("netbox_packer", "0022_update_fileserver_package_settings_comment")' in src
    assert 'name="install_nms_agent"' in src
    assert "default=False" in src
    assert 'name="nms_agent_backend_url"' in src
    assert 'default="https://backend.nms.nmulti.cloud"' in src
    assert 'schemes=["https"]' in src
    assert "Enter an HTTPS URL for the NMS agent backend." in src
    assert 'name="provisions_service"' in src
    assert "editable=False" in src


def test_jobs_has_monitoring_injection_functions() -> None:
    src = _read("netbox_packer/jobs.py")
    assert "def _zabbix_agent2_bootstrap(zabbix_server" in src
    assert "def _nms_agent_bootstrap()" in src
    assert "def _inject_monitoring_agents(user_data_yaml" in src
    # Injection function uses deduplication: skip packages if already present.
    assert '"qemu-guest-agent" not in pkgs' in src
    # Zabbix whole-YAML dedup: skip entirely if zabbix-agent2 already in content.
    assert '"zabbix-agent2" not in user_data_yaml' in src
    assert 'getattr(template, "install_nms_agent", False)' in src
    assert '"nms-agent" not in user_data_yaml' not in src
    assert "expected_paths.issubset(existing_paths)" in src
    assert "bootstrap_command in runcmds" in src
    assert 'return ["akvorado.service"]' in src
    # Zabbix bootstrap script uses ServerActive= with the configured server.
    assert "ServerActive=" in src
    # Security: module-level regex guard prevents heredoc break-out via zabbix_server.
    assert "_ZABBIX_SERVER_RE" in src
    assert "raise ValueError" in src
    # Password SSH: every baked image permits password auth (ssh_pwauth), unless
    # the template already declares it. The password itself is never baked.
    assert '"ssh_pwauth" not in config' in src
    assert 'config["ssh_pwauth"] = True' in src


def test_nms_agent_injection_renders_bootstrap_and_deduplicates(monkeypatch) -> None:
    mod = _load_jobs_isolated(monkeypatch)
    template = SimpleNamespace(
        install_qemu_guest_agent=False,
        install_zabbix_agent2=False,
        install_nms_agent=True,
        nms_agent_backend_url="https://backend.nms.nmulti.cloud/",
        provisions_service="akvorado",
    )

    rendered = mod._inject_monitoring_agents("#cloud-config\npackage_update: false\n", template)
    config = yaml.safe_load(rendered.split("\n", 1)[1])
    files = {item["path"]: item for item in config["write_files"]}

    assert set(files) == {
        "/etc/nms-agent/config.yaml",
        "/etc/systemd/system/nms-agent.service",
        "/opt/nmulticloud-nms-agent-bootstrap.sh",
    }
    agent_config = yaml.safe_load(files["/etc/nms-agent/config.yaml"]["content"])
    assert agent_config["backend_url"] == "https://backend.nms.nmulti.cloud"
    assert agent_config["otlp"]["endpoint"] == "https://backend.nms.nmulti.cloud"
    assert agent_config["zabbix"] == {
        "enabled": False,
        "manage_agent2": False,
        "server": "zabbix.nmulti.cloud",
        "host_metadata": "",
    }
    assert agent_config["rpc"]["allowed_units"] == ["akvorado.service"]
    assert files["/etc/nms-agent/config.yaml"]["permissions"] == "0600"

    bootstrap = files["/opt/nmulticloud-nms-agent-bootstrap.sh"]["content"]
    assert "cec1c4c73d8cf301654ecce63e09c3195fd1b8bb" in bootstrap
    assert "readonly GO_VERSION='1.24.13'" in bootstrap
    assert "go${GO_VERSION}.linux-amd64.tar.gz" in bootstrap
    assert "sha256sum --check --strict" in bootstrap
    assert "git -C" in bootstrap and "rev-parse HEAD" in bootstrap
    assert "curl | bash" not in bootstrap
    subprocess.run(["bash", "-n"], input=bootstrap, text=True, check=True)
    assert config["runcmd"].count(["bash", "/opt/nmulticloud-nms-agent-bootstrap.sh"]) == 1

    # The rendered content now references the agent, so a second pass must not
    # add a second config, unit, script, or command.
    rerendered = mod._inject_monitoring_agents(rendered, template)
    rerendered_config = yaml.safe_load(rerendered.split("\n", 1)[1])
    paths = [item["path"] for item in rerendered_config["write_files"]]
    assert len(paths) == len(set(paths)) == 3
    assert rerendered_config["runcmd"].count(["bash", "/opt/nmulticloud-nms-agent-bootstrap.sh"]) == 1


def test_nms_agent_injection_defaults_off_and_rejects_unsafe_backend(monkeypatch) -> None:
    mod = _load_jobs_isolated(monkeypatch)
    base = "#cloud-config\npackage_update: false\n"
    rendered = mod._inject_monitoring_agents(
        base,
        SimpleNamespace(install_qemu_guest_agent=False, install_zabbix_agent2=False),
    )
    assert "/etc/nms-agent/config.yaml" not in rendered

    template = SimpleNamespace(
        install_qemu_guest_agent=False,
        install_zabbix_agent2=False,
        install_nms_agent=True,
        nms_agent_backend_url="https://operator:secret@example.invalid/path",
        provisions_service="",
    )
    try:
        mod._inject_monitoring_agents(base, template)
    except ValueError as exc:
        assert "without credentials" in str(exc)
    else:  # pragma: no cover - unsafe interpolation must fail closed
        raise AssertionError("expected credential-bearing backend URL to be rejected")

    try:
        mod._normalize_nms_agent_backend_url("http://backend.nms.nmulti.cloud")
    except ValueError as exc:
        assert "HTTPS URL" in str(exc)
    else:  # pragma: no cover - plaintext bootstrap transport must fail closed
        raise AssertionError("expected an HTTP backend URL to be rejected")


def test_nms_agent_comment_only_mention_does_not_skip_injection(monkeypatch) -> None:
    mod = _load_jobs_isolated(monkeypatch)
    template = SimpleNamespace(
        install_qemu_guest_agent=False,
        install_zabbix_agent2=False,
        install_nms_agent=True,
        nms_agent_backend_url="https://backend.nms.nmulti.cloud",
        provisions_service="",
    )

    rendered = mod._inject_monitoring_agents(
        "#cloud-config\n# nms-agent is installed only when requested\npackage_update: false\n",
        template,
    )
    config = yaml.safe_load(rendered.split("\n", 1)[1])

    assert {item["path"] for item in config["write_files"]} == {
        "/etc/nms-agent/config.yaml",
        "/etc/systemd/system/nms-agent.service",
        "/opt/nmulticloud-nms-agent-bootstrap.sh",
    }
    assert ["bash", "/opt/nmulticloud-nms-agent-bootstrap.sh"] in config["runcmd"]


def test_nms_agent_partial_injection_is_completed(monkeypatch) -> None:
    mod = _load_jobs_isolated(monkeypatch)
    template = SimpleNamespace(
        install_qemu_guest_agent=False,
        install_zabbix_agent2=False,
        install_nms_agent=True,
        nms_agent_backend_url="https://backend.nms.nmulti.cloud",
        provisions_service="",
    )
    partial = """#cloud-config
write_files:
  - path: /etc/systemd/system/nms-agent.service
    permissions: '0644'
    content: existing-unit
"""

    rendered = mod._inject_monitoring_agents(partial, template)
    config = yaml.safe_load(rendered.split("\n", 1)[1])
    files = {item["path"]: item for item in config["write_files"]}

    assert set(files) == {
        "/etc/nms-agent/config.yaml",
        "/etc/systemd/system/nms-agent.service",
        "/opt/nmulticloud-nms-agent-bootstrap.sh",
    }
    assert files["/etc/systemd/system/nms-agent.service"]["content"] == "existing-unit"
    assert config["runcmd"].count(["bash", "/opt/nmulticloud-nms-agent-bootstrap.sh"]) == 1


def test_akvorado_seed_contract() -> None:
    rel = "netbox_packer/migrations/0024_seed_akvorado_cloud_init.py"
    seed_rel = "netbox_packer/seeds/akvorado-2.4.0-ubuntu-2404.cloud-config.yaml"
    source = _read(rel)
    constants = _literal_assignments(rel)
    seed = _read(seed_rel)
    name, defaults = _packer_template_seed_defaults(rel)

    assert constants["AKVORADO_CLOUD_CONFIG"] == seed
    assert constants["CONFIG_NAME"] == "akvorado-2.4.0-ubuntu-2404-cloud-config"
    assert constants["CONFIG_VERSION"] == "2.4.0"
    assert constants["TEMPLATE_NAME"] == "akvorado-2.4.0-ubuntu-2404"
    assert constants["TEMPLATE_VMID"] == 9070
    assert constants["PROXMOX_ENDPOINT"] == "https://10.0.30.71:8006"
    assert constants["PROXMOX_NODE"] == "10.0.30.71"
    assert name == constants["TEMPLATE_NAME"]
    assert "update_or_create(" not in source
    assert source.count("objects.get_or_create(") == 2
    assert "Akvorado seed naming collision" in source
    assert 'template_expected_values["installer_config_id"] = config.pk' in source

    assert defaults["proxmox_template_id"] == 9070
    assert defaults["proxmox_endpoint"] == "https://10.0.30.71:8006"
    assert defaults["proxmox_node"] == "10.0.30.71"
    assert defaults["storage_pool"] == "local"
    assert defaults["install_qemu_guest_agent"] is True
    assert defaults["install_zabbix_agent2"] is True
    assert defaults["install_nms_agent"] is True
    assert defaults["nms_agent_backend_url"] == "https://backend.nms.nmulti.cloud"
    assert defaults["provisions_service"] == "akvorado"

    cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
    files = {item["path"]: item["content"] for item in cloud_config["write_files"]}
    assert "/etc/apt/sources.list.d/docker.sources" not in files
    compose = yaml.safe_load(files["/opt/akvorado/docker-compose.yml"])
    services = compose["services"]
    assert services["kafka"]["image"] == "apache/kafka:4.2.0"
    assert services["valkey"]["image"] == "valkey/valkey:9.0"
    assert services["clickhouse"]["image"] == "clickhouse/clickhouse-server:26.3"
    for component in (
        "akvorado-console",
        "akvorado-inlet",
        "akvorado-outlet",
        "akvorado-orchestrator",
    ):
        assert services[component]["image"] == "quay.io/akvorado/akvorado:2.4.0"
    assert "redis" not in services
    assert all("latest" not in service.get("image", "") for service in services.values())
    console_ports = services["akvorado-console"]["ports"]
    assert console_ports == ["127.0.0.1:8081:8080/tcp"]
    assert all(port.startswith("127.0.0.1:") for port in console_ports)
    assert "ports" not in services["akvorado-orchestrator"]
    assert set(services["akvorado-inlet"]["ports"]) == {
        "2055:2055/udp",
        "4739:4739/udp",
        "6343:6343/udp",
    }
    assert services["akvorado-outlet"]["ports"] == ["10179:10179/tcp"]

    console_config = yaml.safe_load(files["/opt/akvorado/config/console.yaml"])
    # Akvorado names its RESP-compatible cache driver "redis"; the actual
    # server and image are Valkey.
    assert console_config["http"]["cache"] == {
        "type": "redis",
        "server": "valkey:6379",
    }
    assert "default-user" not in files["/opt/akvorado/config/console.yaml"].lower()
    assert "anonymous" not in files["/opt/akvorado/config/console.yaml"].lower()
    service = files["/etc/systemd/system/akvorado.service"]
    assert "ExecStart=/usr/bin/docker compose -f /opt/akvorado/docker-compose.yml up -d" in service
    assert "ExecStop=/usr/bin/docker compose -f /opt/akvorado/docker-compose.yml down" in service
    assert "RemainAfterExit=yes" in service

    install_script = files["/usr/local/sbin/install-akvorado-stack"]
    assert "sha256sum --check --strict" in install_script
    assert "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" in install_script
    assert "docker-compose-plugin" in install_script
    assert "systemctl enable --now akvorado.service" in install_script
    assert "curl | bash" not in install_script
    subprocess.run(["bash", "-n"], input=install_script, text=True, check=True)
    assert (
        install_script.index("/etc/apt/keyrings/docker.asc")
        < install_script.index("cat > /etc/apt/sources.list.d/docker.sources")
        < install_script.index("apt-get update")
    )
    assert "nms-agent" not in seed


def test_akvorado_contract_is_documented() -> None:
    required = (
        "akvorado-2.4.0-ubuntu-2404",
        "9070",
        "Kafka `4.2.0`",
        "Valkey `9.0`",
        "ClickHouse `26.3`",
        "akvorado.service",
        "https://backend.nms.nmulti.cloud",
    )
    for rel in (
        "README.md",
        "CLAUDE.md",
        "AGENTS.md",
        "docs/cloud-init-template-images.md",
        "docs/index.md",
    ):
        doc = _read(rel)
        for text in required:
            assert text in doc, f"{rel} must document {text}"


def test_influxdb3_core_debian13_seed_contract() -> None:
    rel = "netbox_packer/migrations/0025_seed_influxdb3_core_debian13_cloud_init.py"
    seed_rel = "netbox_packer/seeds/influxdb-core-3.11.0-debian-13.cloud-config.yaml"
    source = _read(rel)
    constants = _literal_assignments(rel)
    seed = _read(seed_rel)
    name, defaults = _packer_template_seed_defaults(rel)

    # The migration constant is the thing that actually runs; the tracked YAML is
    # the reviewable source of truth. They must not drift.
    assert constants["INFLUXDB3_CORE_DEBIAN13_CLOUD_CONFIG"] == seed
    assert constants["CONFIG_NAME"] == "influxdb-core-3.11.0-debian-13-cloud-config"
    assert constants["CONFIG_VERSION"] == "3.11.0"
    assert constants["TEMPLATE_NAME"] == "influxdb-core-3.11.0-debian-13"
    assert constants["TEMPLATE_VMID"] == 9052
    assert constants["PROXMOX_ENDPOINT"] == ""
    assert constants["PROXMOX_NODE"] == "select-at-build"
    assert name == constants["TEMPLATE_NAME"]
    assert 'dependencies = [\n        ("netbox_packer", "0024_seed_akvorado_cloud_init"),' in source

    # Collision-guarded seeding, 0024 style — never a silent overwrite.
    assert "update_or_create(" not in source
    assert source.count("objects.get_or_create(") == 2
    assert "InfluxDB 3 Core Debian 13 seed naming collision" in source
    assert 'template_expected_values["installer_config_id"] = config.pk' in source
    # A successful bake flips build_status "pending" -> "ready", so comparing
    # mutable build state would make rollback-then-reapply of this migration raise a
    # bogus collision on the row it created itself.
    assert "_MUTABLE_BUILD_STATE_FIELDS" in source
    mutable = _frozenset_members(rel, "_MUTABLE_BUILD_STATE_FIELDS")
    assert "build_status" in mutable
    assert "packer_template_ref" in mutable
    assert "installer_config" in mutable
    assert "field not in _MUTABLE_BUILD_STATE_FIELDS" in source
    # ...but seed identity and configuration are still compared.
    for compared in ("os_family", "os_version", "proxmox_template_id", "storage_pool"):
        assert compared not in mutable, compared

    assert defaults["os_family"] == "debian"
    assert defaults["os_version"] == "13"
    assert defaults["proxmox_template_id"] == 9052
    assert defaults["proxmox_endpoint"] == ""
    assert defaults["proxmox_node"] == "select-at-build"
    assert defaults["storage_pool"] == "local"
    assert defaults["cloud_init_ready"] is True
    assert defaults["build_status"] == "pending"
    assert defaults["install_qemu_guest_agent"] is True
    # OFF because the shared injectors are Ubuntu/amd64-only — see the composed
    # cloud-config test below, which proves the injection really stays out.
    assert defaults["install_zabbix_agent2"] is False
    assert defaults["install_nms_agent"] is False
    assert defaults["provisions_service"] == "influxdb3-core"

    # The seeded os_family/os_version pair must be one the form actually offers,
    # otherwise the template cannot be edited in the UI without being "corrected".
    choices_source = _read("netbox_packer/choices.py")
    assert 'CHOICE_DEBIAN = "debian"' in choices_source
    assert '("13", "Debian 13 (Trixie)")' in choices_source

    # No VMID may be claimed twice across the whole seeded catalog. Collecting only
    # top-level "*VMID*" constants silently missed the tuple-driven seeds (0016) and
    # the profile-dict seeds (0020), so a collision with 9040-9042 or 9050-9051
    # would have passed while the test still claimed whole-catalog coverage.
    seeded_vmids = _all_seeded_vmids()
    # Prove the sweep sees the values the old one could not, before trusting it.
    for previously_missed in (9040, 9041, 9042, 9050, 9051):
        assert previously_missed in seeded_vmids, (previously_missed, sorted(seeded_vmids))
    assert 9052 in seeded_vmids
    duplicates = sorted({v for v in seeded_vmids if seeded_vmids.count(v) > 1})
    assert not duplicates, duplicates

    cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
    assert seed.startswith("#cloud-config\n")
    assert cloud_config["package_update"] is False
    assert cloud_config["package_upgrade"] is False
    files = {item["path"]: item["content"] for item in cloud_config["write_files"]}
    install_script = files["/usr/local/sbin/install-influxdb3-core"]
    assert cloud_config["runcmd"] == [["bash", "/usr/local/sbin/install-influxdb3-core"]]
    subprocess.run(["bash", "-n"], input=install_script, text=True, check=True)

    # Debian 13 gate: this image must refuse any other release rather than
    # half-configuring it.
    assert "ID=${ID:-unknown}" in install_script
    assert "13|13.*)" in install_script
    assert "VERSION_ID=${VERSION_ID:-unknown}" in install_script
    assert "amd64|arm64)" in install_script
    assert "/run/systemd/system" in install_script

    # Repository trust: exactly ONE key, pinned to the expected fingerprint.
    # Proving the downloaded file merely *contains* the fingerprint and then
    # dearmoring all of it would also trust an attacker key bundled alongside the
    # genuine one, which could sign repository metadata and gain root at install.
    assert "24C975CBA61A024EE1B631787C3D57159FC2F927" in install_script
    assert "gpg --dearmor" not in install_script
    assert "--export --export-options export-minimal" in install_script
    assert "GNUPGHOME=" in install_script
    assert "grep -c '^pub:'" in install_script
    assert "test \"${exported_primaries}\" = '1'" in install_script

    # Version pin: a FINAL release only. A tilde sorts before the release it
    # qualifies, so "3.11.0~rc1" is a prerelease and must be refused.
    assert "apt-cache madison" in install_script
    assert "3[.]11[.]0(-[0-9A-Za-z.+]+)?$" in install_script
    assert "Refusing prerelease" in install_script
    assert "3.11.0|3.11.0-*)" in install_script
    assert "3.11.0[-+~]*" not in install_script
    assert 'apt-get install -y --no-install-recommends "${PACKAGE_NAME}=${package_version}"' in install_script
    assert 'apt-mark hold "${PACKAGE_NAME}"' in install_script
    assert "latest" not in install_script
    # The key must be trusted before the source list is written and used.
    assert (
        install_script.index("influxdata-archive.key")
        < install_script.index('> "${SOURCE_FILE}"')
        < install_script.index("apt-get update")
    )

    # Production posture, not package defaults.
    assert "HTTP_BIND='127.0.0.1:8181'" in install_script
    assert 'http-bind = "${HTTP_BIND}"' in install_script
    assert "disable-telemetry-upload = true" in install_script
    assert "plugin-dir intentionally omitted" in install_script
    assert "plugin-dir =" not in install_script
    assert "20-production.conf" in install_script
    assert "Restart=on-failure" in install_script
    assert 'systemctl enable --now "${SERVICE_NAME}"' in install_script
    assert '"http://${HTTP_BIND}/ready"' in install_script
    # node-id must come from a genuinely per-VM source. The Proxmox clone pipeline
    # reuses this template's cicustom meta-data, so the hostname is shared across
    # clones and must NOT be the identity source.
    assert "hostname -s" not in install_script
    assert "/sys/class/dmi/id/product_uuid" in install_script
    assert "/etc/machine-id" in install_script
    assert 'node-id = "${node_id}"' in install_script
    assert 'node_id="influxdb3-${node_suffix}"' in install_script
    # Fails closed rather than minting a colliding identity.
    assert "Cannot derive a unique node id" in install_script

    # EVERY curl in the installer must be time-bounded, not just the readiness
    # probe. This script is the final runcmd entry, so a curl that never returns
    # hangs cloud-init forever and the clone never reaches the diagnostics below;
    # --retry does not help, because a server that completes TLS and then stops
    # sending data produces no error to retry.
    curl_invocations = [
        line for line in _logical_shell_lines(install_script) if not line.lstrip().startswith("#") and "curl " in line
    ]
    assert len(curl_invocations) >= 2, curl_invocations
    for invocation in curl_invocations:
        for flag in ("--connect-timeout", "--max-time"):
            assert flag in invocation, (flag, invocation[:120])
    # The key download additionally caps total retry time and response size, so a
    # hostile endpoint cannot fill the temp directory before fingerprint filtering.
    key_download = next(inv for inv in curl_invocations if "influxdata-archive.key" in inv)
    assert "--retry-max-time" in key_download
    assert "--max-filesize" in key_download
    # The readiness loop has an overall deadline on top of the per-probe bounds.
    assert "readiness_deadline=$((SECONDS + 180))" in install_script
    assert "seq 1 60" not in install_script

    # Credential-free: assert against executable lines only, since the prose
    # comments legitimately explain that token authentication stays enabled.
    code = "\n".join(line for line in install_script.splitlines() if not line.lstrip().startswith("#"))
    for pattern in (
        r"create\s+token",
        r"--token",
        r"admin-token",
        r"TOKEN=",
        r"openssl\s+rand",
        r"password",
        r"passphrase",
        r"tls-cert",
        r"tls-key",
        r"api/v2/setup",
    ):
        assert re.search(pattern, code, re.IGNORECASE) is None, pattern


def _all_seeded_vmids() -> list[int]:
    """Every proxmox_template_id any seed migration assigns, however it is written.

    Evaluates each migration's AST rather than matching variable names, so
    tuple-driven loops and profile dictionaries are included. Raises rather than
    skipping if a migration cannot be parsed — a sweep that quietly covers less
    than it claims is worse than no sweep.
    """

    vmids: list[int] = []
    for migration in sorted((PKG / "migrations").glob("0*.py")):
        rel = f"netbox_packer/migrations/{migration.name}"
        tree = ast.parse(_read(rel))
        constants = _literal_assignments(rel)
        for node in ast.walk(tree):
            # "proxmox_template_id": <value> inside any defaults dict.
            if isinstance(node, ast.Dict):
                for key_node, value_node in zip(node.keys, node.values, strict=True):
                    if key_node is None:
                        continue
                    try:
                        key = ast.literal_eval(key_node)
                    except ValueError:
                        continue
                    if key not in {"proxmox_template_id", "vmid"}:
                        continue
                    try:
                        value = _resolve_static_value(value_node, constants)
                    except ValueError:
                        continue
                    if isinstance(value, int) and not isinstance(value, bool):
                        vmids.append(value)
            # Tuple-driven seed loops: (name, os_version, vmid).
            if isinstance(node, ast.Tuple):
                try:
                    values = [ast.literal_eval(element) for element in node.elts]
                except ValueError:
                    continue
                for value in values:
                    if isinstance(value, int) and not isinstance(value, bool) and 9000 <= value <= 9999:
                        vmids.append(value)
    assert vmids, "VMID sweep found nothing — the extraction is broken"
    return vmids


def test_seeded_vmid_sweep_would_catch_a_duplicate() -> None:
    """Mutation check on the guard itself: a duplicate must be detectable.

    The sweep is only useful if it both sees every seeded VMID and reports a
    collision, so assert the detection logic on a known-duplicate list rather than
    trusting that the clean catalog passing means anything.
    """

    seeded = _all_seeded_vmids()
    assert len(seeded) >= 14, sorted(seeded)
    injected = seeded + [9052]
    duplicates = sorted({v for v in injected if injected.count(v) > 1})
    assert duplicates == [9052]


def test_influxdb3_core_debian13_build_resolves_a_debian_13_image(monkeypatch) -> None:
    """The bake must not silently use Bookworm for an os_version="13" template.

    A cloud_config bake never executes cloud-init, so a wrong base image produces
    an artifact that can still be marked ready and only fails its OS gate later, at
    clone time.
    """

    jobs = _load_jobs_isolated(monkeypatch)
    template = _template(os_family="debian", os_version="13")

    url, sha = jobs._resolve_cloud_image_source(template, None)

    assert "trixie" in url
    assert "debian-13-genericcloud" in url
    assert "bookworm" not in url and "debian-12" not in url
    assert sha == ""
    # Other releases keep working, and an unknown one fails loudly instead of
    # falling back to some arbitrary image.
    assert "bookworm" in jobs._resolve_cloud_image_source(_template(os_family="debian", os_version="12"), None)[0]
    assert "bullseye" in jobs._resolve_cloud_image_source(_template(os_family="debian", os_version="11"), None)[0]
    with pytest.raises(RuntimeError):
        jobs._resolve_cloud_image_source(_template(os_family="debian", os_version="99"), None)
    # An explicit override still wins — but only with a digest (see the pin tests).
    assert jobs._resolve_cloud_image_source(
        template, {"image_url": "http://x/y.qcow2", "image_sha256": "a" * 64}
    ) == ("http://x/y.qcow2", "a" * 64)


def test_influxdb3_core_debian13_injected_cloud_config_stays_debian_safe(
    monkeypatch,
) -> None:
    """Assert on the FULLY INJECTED config, not just the pristine seed.

    Build-time injection is what actually reaches the guest. Two properties matter:
    the Ubuntu/amd64-only injections must stay out of a Debian 13 (and arm64)
    image, and this installer must remain the LAST runcmd entry — cloud-init
    shellifies runcmd into a plain /bin/sh script with no `set -e`, so a
    non-final failure would be masked by a later command's success.
    """

    jobs = _load_jobs_isolated(monkeypatch)
    seed = _read("netbox_packer/seeds/influxdb-core-3.11.0-debian-13.cloud-config.yaml")
    _name, defaults = _packer_template_seed_defaults(
        "netbox_packer/migrations/0025_seed_influxdb3_core_debian13_cloud_init.py"
    )
    template = SimpleNamespace(
        os_family=defaults["os_family"],
        os_version=defaults["os_version"],
        install_qemu_guest_agent=defaults["install_qemu_guest_agent"],
        install_zabbix_agent2=defaults["install_zabbix_agent2"],
        install_nms_agent=defaults["install_nms_agent"],
        zabbix_server="zabbix.nmulti.cloud",
        nms_agent_backend_url="",
        provisions_service=defaults["provisions_service"],
    )

    injected = jobs._inject_monitoring_agents(seed, template)
    config = yaml.safe_load(injected.split("\n", 1)[1])

    # The Ubuntu Zabbix package name and the amd64-only NMS agent must not appear.
    assert "zabbix-agent2" not in injected
    assert "ubuntu${VERSION_ID}" not in injected
    assert "nms-agent" not in injected
    assert "go.dev/dl" not in injected

    runcmds = [str(entry) for entry in config["runcmd"]]
    assert any("install-influxdb3-core" in entry for entry in runcmds)
    # LAST entry: with no `set -e` in cloud-init's wrapper, the wrapper's exit
    # status is the final command's, so a failing install must not be followed by
    # anything that could report success over it.
    assert "install-influxdb3-core" in runcmds[-1], runcmds
    # QEMU guest agent is a plain Debian package, so its injection is expected.
    assert any("qemu-guest-agent" in entry for entry in runcmds)
    # Password SSH is still enabled for clone-time credentials.
    assert config.get("ssh_pwauth") is True


def test_influxdb3_explorer_debian13_seed_contract() -> None:
    rel = "netbox_packer/migrations/0030_seed_influxdb3_explorer_debian13_cloud_init.py"
    seed_rel = "netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml"
    source = _read(rel)
    constants = _literal_assignments(rel)
    seed = _read(seed_rel)
    name, defaults = _packer_template_seed_defaults(rel)
    # The migration executes the embedded constant; the tracked YAML is the
    # reviewable source of truth, so any byte of drift is a defect.
    assert constants["INFLUXDB3_EXPLORER_DEBIAN13_CLOUD_CONFIG"] == seed
    assert constants["CONFIG_NAME"] == "influxdb3-explorer-1.9.0-debian-13-cloud-config"
    assert constants["CONFIG_VERSION"] == "1.9.0"
    assert constants["TEMPLATE_NAME"] == "influxdb3-explorer-1.9.0-debian-13"
    assert constants["TEMPLATE_VMID"] == 9053
    assert constants["PROXMOX_ENDPOINT"] == ""
    assert constants["PROXMOX_NODE"] == "select-at-build"
    assert name == constants["TEMPLATE_NAME"]
    assert 'dependencies = [\n        ("netbox_packer", "0029_pin_influxdb3_debian13_base_image"),' in source

    # Collision-guarded seed: existing operator rows are compared and reported,
    # never silently overwritten. Machine-written build state is not identity.
    assert "update_or_create(" not in source
    assert source.count("objects.get_or_create(") == 2
    assert "InfluxDB 3 Explorer Debian 13 seed naming collision" in source
    assert 'template_expected_values["installer_config_id"] = config.pk' in source
    mutable = _frozenset_members(rel, "_MUTABLE_BUILD_STATE_FIELDS")
    assert mutable == {"installer_config", "build_status", "packer_template_ref"}
    assert "field not in _MUTABLE_BUILD_STATE_FIELDS" in source

    assert defaults["os_family"] == "debian"
    assert defaults["os_version"] == "13"
    assert defaults["proxmox_template_id"] == 9053
    assert defaults["proxmox_endpoint"] == ""
    assert defaults["proxmox_node"] == "select-at-build"
    assert defaults["storage_pool"] == "local"
    assert defaults["cloud_init_ready"] is True
    assert defaults["build_status"] == "pending"
    assert defaults["install_qemu_guest_agent"] is True
    assert defaults["install_zabbix_agent2"] is False
    assert defaults["install_nms_agent"] is False
    assert defaults["provisions_service"] == "influxdb3-explorer"

    seeded_vmids = _all_seeded_vmids()
    assert 9053 in seeded_vmids
    duplicates = sorted({vmid for vmid in seeded_vmids if seeded_vmids.count(vmid) > 1})
    assert not duplicates, duplicates

    assert seed.startswith("#cloud-config\n")
    cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
    assert cloud_config["package_update"] is False
    assert cloud_config["package_upgrade"] is False
    files = {item["path"]: item["content"] for item in cloud_config["write_files"]}
    installer_path = "/usr/local/sbin/install-influxdb3-explorer"
    runner_path = "/usr/local/sbin/run-influxdb3-explorer"
    installer = files[installer_path]
    runner = files[runner_path]
    service = files["/etc/systemd/system/influxdb3-explorer.service"]
    environment = files["/etc/default/influxdb3-explorer"]
    provisioning = files["/usr/share/doc/netbox-packer/influxdb3-explorer-provisioning.txt"]
    assert cloud_config["runcmd"] == [["bash", installer_path]]
    subprocess.run(["bash", "-n"], input=installer, text=True, check=True)
    subprocess.run(["bash", "-n"], input=runner, text=True, check=True)

    # Correct upstream repository, immutable final release, and no fallback tag.
    assert seed.count("readonly EXPLORER_IMAGE_REPOSITORY='influxdata/influxdb3-ui'") == 2
    assert seed.count(
        "readonly EXPLORER_IMAGE_DIGEST="
        "'sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc'"
    ) == 2
    assert seed.count('readonly EXPLORER_IMAGE="${EXPLORER_IMAGE_REPOSITORY}@${EXPLORER_IMAGE_DIGEST}"') == 2
    assert "influxdata/influxdb3-explorer" not in seed
    assert "influxdata/explorer" not in seed
    assert "influxdata/influxdb-explorer" not in seed
    assert "latest" not in seed.lower()
    assert "latest" not in source.lower()
    assert '--pull=never' in runner
    assert '/usr/bin/docker pull "${EXPLORER_IMAGE}"' in installer
    assert '/usr/bin/docker image inspect "${EXPLORER_IMAGE}"' in installer

    # Debian 13 and the two architectures covered by the supplied manifest only.
    assert "ID=${ID:-unknown}" in installer
    assert "13|13.*)" in installer
    assert "amd64|arm64)" in installer
    assert "/run/systemd/system" in installer
    # Docker is installed solely from Debian's signed repositories.
    assert "docker.io" in installer
    assert "download.docker.com" not in seed
    assert "get.docker.com" not in seed
    assert not re.search(r"curl[^\n|]*\|\s*(?:ba)?sh", installer)
    # Explorer 1.9.0 runs as non-root uid/gid 1500. The writable database must
    # belong to it, while provisioned config remains root-owned and readable by
    # only the Explorer group through the read-only container mount.
    assert "EXPLORER_UID='1500'" in installer
    assert "EXPLORER_GID='1500'" in installer
    assert '-o root -g "${EXPLORER_GID}" -m 0750 /etc/influxdb3-explorer' in installer
    assert '-o "${EXPLORER_UID}" -g "${EXPLORER_GID}" -m 0700' in installer

    # Silent apt or registry stalls cannot hold first boot forever. apt has
    # connection/retry bounds, every long-running fetch has an overall timeout,
    # and an unexpectedly large pulled image is rejected before service startup.
    for apt_bound in (
        "Acquire::Retries=3",
        "Acquire::http::Timeout=30",
        "Acquire::https::Timeout=30",
    ):
        assert apt_bound in installer
    assert installer.count("timeout --signal=TERM --kill-after=30s 600s") == 3
    assert "MAX_IMAGE_SIZE_BYTES='2147483648'" in installer
    assert '"${pulled_size}" -gt "${MAX_IMAGE_SIZE_BYTES}"' in installer
    curl_invocations = [
        line
        for line in _logical_shell_lines(installer)
        if not line.lstrip().startswith("#") and re.search(r"(?:^|\bif\s+)curl\s", line)
    ]
    assert len(curl_invocations) == 1, curl_invocations
    for flag in ("--connect-timeout", "--max-time"):
        assert flag in curl_invocations[0]
    assert "readiness_deadline=$((SECONDS + 180))" in installer

    # A failed final installer remains visible even if cloud-init's wrapper ever
    # gains later commands. It is an EXIT trap (explicit exits bypass ERR), with
    # signal conversion and a durable marker; the script itself is retained.
    assert "set -Eeuo pipefail" in installer
    assert "trap on_install_exit EXIT" in installer
    assert not re.search(r"^\s*trap\s+\S+\s+ERR\b", installer, re.MULTILINE)
    exit_traps = [line for line in installer.splitlines() if re.match(r"\s*trap\s+\S+\s+EXIT\b", line)]
    assert exit_traps == ["trap on_install_exit EXIT"]
    for signal, code in (("TERM", 143), ("INT", 130), ("HUP", 129)):
        assert f"trap 'exit {code}' {signal}" in installer
    assert "/var/lib/nms/influxdb-install-failed" in installer
    assert not any("rm" in str(entry) for entry in cloud_config["runcmd"])

    # systemd is the sole lifecycle owner. The published host address is
    # configurable but loopback by default; only documented container port 8080
    # is exposed. Persistent and provisioned state have separate mounts.
    assert "Requires=docker.service" in service
    assert "ExecStart=/usr/local/sbin/run-influxdb3-explorer" in service
    assert "ExecStop=-/usr/bin/docker stop --time 30 influxdb3-explorer" in service
    assert "Restart=on-failure" in service
    assert "EXPLORER_HOST_BIND=127.0.0.1" in environment
    assert "0.0.0.0" not in seed
    assert 'publish_address="${host_bind}:8080:${CONTAINER_PORT}/tcp"' in runner
    assert "CONTAINER_PORT='8080'" in runner
    assert "8443" not in runner
    assert "--volume /var/lib/influxdb3-explorer:/db:rw" in runner
    assert "--volume /etc/influxdb3-explorer:/app-root/config:ro" in runner

    # The exact existing secret-reference boundary: the RPC returns only an
    # nms-secret:<opaque-id> reference, which is resolved after cloning. Neither
    # that per-instance reference nor a resolved credential is a write_files item.
    assert "service.influxdb.1.token_create" in seed
    assert "nms-secret:<opaque-id>" in seed
    assert "/etc/influxdb3-explorer/config.json" in provisioning
    assert "/etc/influxdb3-explorer/config.json" not in files
    executable = "\n".join((installer, runner, service, environment))
    for pattern in (
        r"DEFAULT_API_TOKEN",
        r"INFLUXDB\w*_TOKEN",
        r"SESSION_SECRET_KEY",
        r"Authorization:\s*Bearer",
        r"--token(?:=|\s)",
        r"password\s*=",
        r"private[-_ ]key",
        r"BEGIN [A-Z ]*PRIVATE KEY",
        r"https?://[^\s/@]+:[^\s/@]+@",
        r"nms-secret:(?!<opaque-id>)\S+",
    ):
        assert re.search(pattern, executable, re.IGNORECASE) is None, pattern
    assert "8181" not in executable, "a Core URL must arrive only at provision time"


def test_influxdb3_explorer_injected_cloud_config_stays_debian_safe(
    monkeypatch,
) -> None:
    jobs = _load_jobs_isolated(monkeypatch)
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    _name, defaults = _packer_template_seed_defaults(
        "netbox_packer/migrations/0030_seed_influxdb3_explorer_debian13_cloud_init.py"
    )
    template = SimpleNamespace(
        os_family=defaults["os_family"],
        os_version=defaults["os_version"],
        install_qemu_guest_agent=defaults["install_qemu_guest_agent"],
        install_zabbix_agent2=defaults["install_zabbix_agent2"],
        install_nms_agent=defaults["install_nms_agent"],
        zabbix_server="zabbix.nmulti.cloud",
        nms_agent_backend_url="",
        provisions_service=defaults["provisions_service"],
    )

    injected = jobs._inject_monitoring_agents(seed, template)
    config = yaml.safe_load(injected.split("\n", 1)[1])

    assert "zabbix-agent2" not in injected
    assert "ubuntu${VERSION_ID}" not in injected
    assert "nms-agent" not in injected
    assert "go.dev/dl" not in injected
    runcmds = [str(entry) for entry in config["runcmd"]]
    assert any("qemu-guest-agent" in entry for entry in runcmds)
    assert "install-influxdb3-explorer" in runcmds[-1], runcmds
    assert config.get("ssh_pwauth") is True

    # The final-payload boundary accepts the pristine, fully injected profile.
    jobs._validate_influxdb3_explorer_payload(injected)


@pytest.mark.parametrize(
    ("payload_change", "expected_reason"),
    (
        ({"api_token": "plaintext-token"}, "credential-bearing YAML key"),
        (
            {
                "path": "/tmp/explorer-token.env",
                "content": "DEFAULT_API_TOKEN=plaintext-token\n",
            },
            "credential-bearing value",
        ),
        (
            {
                "path": "/tmp/core.env",
                "content": "INFLUXDB_CORE_HOST=core.internal\n",
            },
            "Core endpoint setting",
        ),
        (
            {
                "path": "/tmp/private-key.pem",
                "content": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
            },
            "private key material",
        ),
        (
            {
                "path": "/tmp/secret-ref",
                "content": "nms-secret:environment-specific-id\n",
            },
            "non-placeholder nms-secret",
        ),
        (
            {
                "path": "/tmp/encoded",
                "encoding": "b64",
                "content": "cGxhaW50ZXh0LXRva2Vu",
            },
            "encoded write_files content",
        ),
        (
            {
                "path": "/etc/influxdb3-explorer/config.json",
                "content": "{}\n",
            },
            "writes Explorer config.json",
        ),
        (
            {
                "path": "/tmp/connection.env",
                "content": "EXPLORER_CONNECTION=https://core.internal\n",
            },
            "unapproved connection URL",
        ),
    ),
)
def test_influxdb3_explorer_final_payload_guard_rejects_credentials(
    monkeypatch,
    payload_change,
    expected_reason,
) -> None:
    jobs = _load_jobs_isolated(monkeypatch)
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    config = yaml.safe_load(seed.split("\n", 1)[1])
    if "path" in payload_change:
        config["write_files"].append(payload_change)
    else:
        config.update(payload_change)
    tainted = "#cloud-config\n" + yaml.safe_dump(config, sort_keys=False)

    with pytest.raises(RuntimeError, match=expected_reason):
        jobs._validate_influxdb3_explorer_payload(tainted)


def test_influxdb3_explorer_payload_guard_resists_encoding_and_path_aliases(
    monkeypatch,
) -> None:
    """The two bypasses round 2 found must stay closed.

    Both defeated a denylist rather than a missing rule, which is why the guard now ends
    with a write-path allowlist:

    * ``content: !!binary`` loads as ``bytes`` and survives the safe-load/safe-dump
      injection cycle, so a scan that skipped non-string scalars never inspected it.
    * ``/etc/influxdb3-explorer/./config.json`` names the same file as the plain path, so
      an exact string comparison did not recognise it.
    """

    jobs = _load_jobs_isolated(monkeypatch)
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    base = yaml.safe_load(seed.split("\n", 1)[1])
    assert len(base["write_files"]) == 5, "mutation target: pristine Explorer write_files changed"
    allowed = {entry["path"] for entry in base["write_files"]}

    # A base64-encoded Core URL + token, carried as a YAML binary scalar so no `encoding`
    # key is present and the value is bytes rather than str.
    secret = json.dumps({"url": "http://core.internal:8181", "token": "apiv3-plaintext"}).encode()
    binary_payload = copy.deepcopy(base)
    binary_payload["write_files"].append(
        {
            "path": "/usr/local/sbin/run-influxdb3-explorer",  # an ALLOWED path, so only
            "permissions": "0755",  # the content rule can catch this
            "owner": "root:root",
            "content": secret,
        }
    )
    rendered = "#cloud-config\n" + yaml.safe_dump(binary_payload, sort_keys=False)
    assert "!!binary" in rendered, "mutation target: payload no longer round-trips as binary"
    with pytest.raises(RuntimeError, match="non-text content"):
        jobs._validate_influxdb3_explorer_payload(rendered)

    # A path alias for the forbidden config.json, plus an entirely unexpected path. Both
    # must be refused, the alias by canonicalisation and the other by the allowlist.
    for alias in ("/etc/influxdb3-explorer/./config.json", "/etc/../etc/influxdb3-explorer/config.json"):
        assert alias not in allowed
        assert posixpath.normpath(alias) not in allowed
        aliased = copy.deepcopy(base)
        aliased["write_files"].append(
            {"path": alias, "permissions": "0640", "owner": "root:root", "content": "{}\n"}
        )
        with pytest.raises(RuntimeError):
            jobs._validate_influxdb3_explorer_payload(
                "#cloud-config\n" + yaml.safe_dump(aliased, sort_keys=False)
            )

    # The pristine seed must still pass, or the allowlist is simply refusing everything.
    jobs._validate_influxdb3_explorer_payload(seed)


def test_influxdb3_explorer_tainted_final_payload_never_reaches_proxbox(
    monkeypatch,
) -> None:
    jobs = _load_jobs_isolated(monkeypatch)
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    config = yaml.safe_load(seed.split("\n", 1)[1])
    assert len(config["write_files"]) == 5, "mutation target: pristine Explorer write_files changed"
    plaintext_token = "round1-plaintext-token"
    config["write_files"].append(
        {
            "path": "/etc/influxdb3-explorer/config.json",
            "permissions": "0640",
            "owner": "root:1500",
            "content": json.dumps(
                {
                    "url": "http://core.internal:8181",
                    "token": plaintext_token,
                }
            ),
        }
    )
    tainted = "#cloud-config\n" + yaml.safe_dump(config, sort_keys=False)

    settings_row = SimpleNamespace(
        proxbox_api_url="https://proxbox.example",
        get_fileserver_package_read_token=lambda: "",
        get_proxbox_api_key=lambda: "api-key",
    )
    models_module = ModuleType("netbox_packer.models")
    models_module.PackerPluginSettings = type(
        "PackerPluginSettings",
        (),
        {"get_solo": staticmethod(lambda: settings_row)},
    )
    models_module.PackerTemplate = type("PackerTemplate", (), {})
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models_module)

    call_proxbox_build = Mock(return_value={"status": "completed", "vmid": 9053})
    client_module = ModuleType("netbox_packer.proxbox_client")
    client_module.ProxboxApiError = type("ProxboxApiError", (Exception,), {})
    client_module.call_proxbox_build = call_proxbox_build
    monkeypatch.setitem(sys.modules, "netbox_packer.proxbox_client", client_module)

    installer = SimpleNamespace(
        content=tainted,
        installer_type="cloud_config",
        checksum="e" * 64,
    )
    template = SimpleNamespace(
        pk=53,
        name="influxdb3-explorer-1.9.0-debian-13",
        installer_config=installer,
        storage_pool="local",
        proxmox_node="node1",
        proxmox_endpoint="",
        proxmox_template_id=9053,
        os_family="debian",
        os_version="13",
        base_image_url="",
        base_image_sha256="",
        is_fileserver_golden_template=False,
        install_qemu_guest_agent=True,
        install_zabbix_agent2=False,
        zabbix_server="",
        install_nms_agent=False,
        provisions_service="influxdb3-explorer",
    )
    build = SimpleNamespace(
        variable_overrides={"endpoint_id": 17, "target_node": "node1"},
        log="",
        save=Mock(),
    )

    with pytest.raises(RuntimeError, match="credential-free boundary"):
        jobs.PackerBuildJob()._run_proxbox_cloud_build(build, template, "node1", 60)

    call_proxbox_build.assert_not_called()
    build.save.assert_called_once_with(update_fields=["log"])
    assert "Refusing InfluxDB 3 Explorer bake" in build.log
    assert "config.json only after cloning" in build.log
    assert plaintext_token not in build.log


@pytest.mark.parametrize(
    ("host_bind", "expected_publish"),
    (
        (None, "127.0.0.1:8080:8080/tcp"),
        ("10.20.30.40", "10.20.30.40:8080:8080/tcp"),
        ("::1", "[::1]:8080:8080/tcp"),
    ),
)
def test_influxdb3_explorer_runner_applies_configured_bind(
    tmp_path: Path,
    host_bind: str | None,
    expected_publish: str,
) -> None:
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
    files = {item["path"]: item["content"] for item in cloud_config["write_files"]}
    runner = files["/usr/local/sbin/run-influxdb3-explorer"]
    seeded_environment = files["/etc/default/influxdb3-explorer"]

    # Exercise the shell behavior with a recording Docker stub. Assert the
    # mutation target exists first so this cannot become a false-positive test.
    assert runner.count("/usr/bin/docker") == 1
    docker_stub = tmp_path / "docker"
    docker_stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    docker_stub.chmod(0o755)
    instrumented = runner.replace("/usr/bin/docker", str(docker_stub), 1)
    environment = {"PATH": "/usr/bin:/bin"}
    if host_bind is None:
        seeded_bind = re.search(r"^EXPLORER_HOST_BIND=(\S+)$", seeded_environment, re.MULTILINE)
        assert seeded_bind, "seeded bind setting disappeared"
        environment["EXPLORER_HOST_BIND"] = seeded_bind.group(1)
    else:
        environment["EXPLORER_HOST_BIND"] = host_bind

    result = subprocess.run(
        ["bash"],
        input=instrumented,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    arguments = result.stdout.splitlines()
    assert arguments[0] == "run"
    assert expected_publish in arguments
    expected_image = (
        "influxdata/influxdb3-ui@"
        "sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc"
    )
    assert expected_image in arguments


def test_influxdb3_explorer_runner_rejects_shell_metacharacters() -> None:
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
    runner = next(
        item["content"]
        for item in cloud_config["write_files"]
        if item["path"] == "/usr/local/sbin/run-influxdb3-explorer"
    )

    result = subprocess.run(
        ["bash"],
        input=runner,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "EXPLORER_HOST_BIND": "127.0.0.1;id"},
        check=False,
    )

    assert result.returncode == 1
    assert "Invalid EXPLORER_HOST_BIND" in result.stderr
    assert "uid=" not in result.stdout


@pytest.mark.parametrize(
    ("failure_command", "expected_code"),
    (
        ("exit 41", 41),
        ('kill -s TERM "$$"', 143),
        ('kill -s INT "$$"', 130),
        ('kill -s HUP "$$"', 129),
    ),
)
def test_influxdb3_explorer_installer_failure_trap_records_every_exit(
    tmp_path: Path,
    failure_command: str,
    expected_code: int,
) -> None:
    seed = _read("netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml")
    cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
    installer = next(
        item["content"]
        for item in cloud_config["write_files"]
        if item["path"] == "/usr/local/sbin/install-influxdb3-explorer"
    )
    prefix, trap_line, _remainder = installer.partition("trap on_install_exit EXIT")
    assert trap_line, "EXIT-trap mutation target disappeared"
    assert "install -d -m 0755 /var/lib/nms || true" in prefix
    marker = tmp_path / "influxdb-install-failed"
    harness = (prefix + trap_line).replace(
        "readonly NMS_FAILURE_MARKER='/var/lib/nms/influxdb-install-failed'",
        f"readonly NMS_FAILURE_MARKER='{marker}'",
        1,
    ).replace(
        "install -d -m 0755 /var/lib/nms || true",
        'install -d -m 0755 "${NMS_FAILURE_MARKER%/*}" || true',
        1,
    )

    result = subprocess.run(
        ["bash"],
        input=f"{harness}\n{failure_command}\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == expected_code, result.stderr
    marker_content = marker.read_text(encoding="utf-8")
    assert f"exit_code: {expected_code}\n" in marker_content
    assert "installer: bash\n" in marker_content
    assert re.search(r"failed_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", marker_content)


def test_injected_zabbix_download_is_bounded() -> None:
    """The injected Zabbix bootstrap runs on nearly every template.

    It is appended after each template's own installer, so an unbounded download there
    can hang first boot for any image that opts into Zabbix — not just the InfluxDB
    profiles this branch hardens.
    """

    jobs_src = _read("netbox_packer/jobs.py")
    zabbix_download = next(
        line for line in _logical_shell_lines(jobs_src) if "zabbix-release.deb" in line and "curl" in line
    )
    for flag in ("--connect-timeout", "--max-time", "--retry-max-time", "--max-filesize"):
        assert flag in zabbix_download, (flag, zabbix_download[:120])


def test_influxdb_0020_profiles_are_hardened_to_0025_parity() -> None:
    """The 0020 profiles must carry the same three fixes as the Debian 13 profile.

    They share its shell shape, so they shared its defects: a keyring trust boundary
    that admitted extra keys, a version match that accepted prereleases, and
    unbounded downloads. Asserted against the tracked seed files and the migration
    constants that must equal them.
    """

    rel = "netbox_packer/migrations/0026_harden_influxdb_0020_profiles.py"
    source = _read(rel)
    constants = _literal_assignments(rel)

    seeds = {
        "HARDENED_OSS2_CLOUD_CONFIG": (
            "netbox_packer/seeds/influxdb-oss-2.9.1-ubuntu-2404.cloud-config.yaml",
            "influxdb2",
            "2.9.1",
            "http://127.0.0.1:8086/health",
        ),
        "HARDENED_CORE3_CLOUD_CONFIG": (
            "netbox_packer/seeds/influxdb-core-3.11.0-ubuntu-2404.cloud-config.yaml",
            "influxdb3-core",
            "3.11.0",
            "http://127.0.0.1:8181/ready",
        ),
    }

    for constant, (seed_rel, package, version, health_url) in seeds.items():
        seed = _read(seed_rel)
        assert constants[constant] == seed, constant

        cloud_config = yaml.safe_load(seed.split("\n", 1)[1])
        files = {item["path"]: item["content"] for item in cloud_config["write_files"]}
        installer = next(iter(files.values()))
        subprocess.run(["bash", "-n"], input=installer, text=True, check=True)

        # 1. exactly one signing key is trusted
        assert "gpg --dearmor" not in installer, constant
        assert "--export --export-options export-minimal" in installer, constant
        assert "GNUPGHOME=" in installer, constant
        assert "grep -c '^pub:'" in installer, constant
        assert "24C975CBA61A024EE1B631787C3D57159FC2F927" in installer, constant

        # 2. final releases only — a tilde version sorts before the release it
        #    qualifies, so accepting one silently installs an unreviewed build
        assert f"Refusing prerelease {package}" in installer, constant
        assert f"{version}|{version}-*)" in installer, constant
        assert f"{version}[-+~]*" not in installer, constant
        assert "([+~-]|$)" not in installer, constant

        # 3. every download bounded, and the readiness loop actually bounded
        invocations = [
            line for line in _logical_shell_lines(installer) if not line.lstrip().startswith("#") and "curl " in line
        ]
        assert len(invocations) >= 2, (constant, invocations)
        for invocation in invocations:
            for flag in ("--connect-timeout", "--max-time"):
                assert flag in invocation, (constant, flag, invocation[:110])
        key_download = next(i for i in invocations if "influxdata-archive.key" in i)
        assert "--retry-max-time" in key_download, constant
        assert "--max-filesize" in key_download, constant
        assert "readiness_deadline=$((SECONDS + 180))" in installer, constant
        assert "seq 1 60" not in installer, constant
        assert health_url in installer, constant

        # 4. an installer failure must leave durable evidence. Cloud-init's runcmd
        #    wrapper has no `set -e` and build-time injection appends the Zabbix
        #    bootstrap AFTER this script, so a non-zero exit here is masked by a later
        #    command's success and cloud-init still reports success.
        assert "set -Eeuo pipefail" in installer, constant
        # An EXIT trap, not an ERR trap: bash does not run an ERR trap for an explicit
        # `exit 1`, and these scripts reject prereleases, unexpected installed versions,
        # and readiness timeouts with exactly that — those paths would record nothing.
        assert "trap on_install_exit EXIT" in installer, constant
        # No ERR trap is *installed* (the comment above it may legitimately mention one).
        assert not re.search(r"^\s*trap\s+\S+\s+ERR\b", installer, re.MULTILINE), constant
        assert "/var/lib/nms/influxdb-install-failed" in installer, constant
        # Exactly ONE EXIT trap: a second would silently replace the first. Signal
        # traps are required alongside it, not forbidden — on an untrapped TERM/INT/HUP
        # bash runs the EXIT trap with `$?` possibly still 0, so the handler would clean
        # up and record nothing. Converting each signal to a non-zero exit is what makes
        # a systemd cancellation, guest shutdown, or external timeout visible.
        exit_traps = [line for line in installer.splitlines() if re.match(r"\s*trap\s+\S+\s+EXIT\b", line)]
        assert len(exit_traps) == 1, (constant, exit_traps)
        for signal, code in (("TERM", 143), ("INT", 130), ("HUP", 129)):
            assert f"trap 'exit {code}' {signal}" in installer, (constant, signal)
        # Every explicit failure exit is inside the trapped script, so each one records.
        assert installer.count("exit 1") >= 3, constant
        # ...and the installer is not deleted, so a failed guest keeps its evidence.
        assert cloud_config["runcmd"] == [["bash", next(iter(files))]], constant
        assert not any("rm" in str(entry) for entry in cloud_config["runcmd"]), constant

    # A row that no longer matches the 0020 baseline is neither rewritten (that would
    # discard an operator edit) nor silently skipped (that would let an operator
    # believe the vector was removed everywhere). It fails the migration by name.
    assert "LEGACY_OSS2_CLOUD_CONFIG" in constants
    assert "LEGACY_CORE3_CLOUD_CONFIG" in constants
    assert "if config.content != legacy:" in source
    assert "unresolved.append(" in source
    assert "if unresolved:" in source
    # The baseline check and the write are separate statements, so the row is locked
    # AND the write re-asserts the baseline as its predicate. Without both, an operator
    # committing a customization between the two would have it silently overwritten.
    assert "select_for_update()" in source
    assert "filter(pk=config.pk, content=legacy).update(" in source
    assert "if not replaced:" in source
    assert "Refusing to leave a known-vulnerable InfluxDB profile in place" in source

    # Rebake invalidation follows the real relationship, not the editable name: a
    # renamed or additional consumer would otherwise keep `ready` and its old artifact.
    assert "installer_config_id=config.pk" in source
    assert "exclude(installer_config_checksum_at_build=hardened_checksum)" in source
    assert "name__in=list(template_names)" not in source

    # A build already in flight read the old content and would write `ready` over the
    # rebake marker, so the migration refuses to race it.
    assert 'status__in=("queued", "running")' in source
    assert "are queued or running against its templates" in source
    # The legacy constants must be the genuine 0020 content, or the equality guard
    # silently never matches and the migration becomes a no-op.
    legacy_020 = _literal_assignments("netbox_packer/migrations/0020_seed_influxdb_profiles.py")
    assert constants["LEGACY_OSS2_CLOUD_CONFIG"] == legacy_020["INFLUXDB_OSS2_CLOUD_CONFIG"]
    assert constants["LEGACY_CORE3_CLOUD_CONFIG"] == legacy_020["INFLUXDB_CORE3_CLOUD_CONFIG"]
    # ...and they must be the *vulnerable* shape, so the guard targets what it claims.
    assert "gpg --dearmor" in constants["LEGACY_OSS2_CLOUD_CONFIG"]
    assert "seq 1 60" in constants["LEGACY_CORE3_CLOUD_CONFIG"]

    # Linked templates are marked for a rebake, and nothing is deleted.
    assert 'build_status="pending"' in source
    assert 'installer_config_checksum_at_build=""' in source
    assert ".delete()" not in source
    assert 'dependencies = [\n        ("netbox_packer", "0025_seed_influxdb3_core_debian13_cloud_init"),' in source
def _template(**overrides):
    """A PackerTemplate stand-in with the base-image pin fields defaulted to empty."""

    fields = {
        "os_family": "ubuntu",
        "os_version": "24.04",
        "base_image_url": "",
        "base_image_sha256": "",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_pinned_base_image_requires_a_verified_digest(monkeypatch) -> None:
    """A pin without a digest must fail the build, not be trusted.

    A pinned URL with no digest is the worst of both worlds: it looks like provenance
    while guaranteeing nothing about the bytes, and it does not even survive the vendor
    replacing the artifact at that URL.
    """

    jobs = _load_jobs_isolated(monkeypatch)
    digest = "b" * 64

    # Pinned on the template, no digest -> refused, and the message names the source.
    with pytest.raises(RuntimeError) as exc_info:
        jobs._resolve_cloud_image_source(_template(base_image_url="https://vendor.example/img.qcow2"), None)
    assert "base_image_url" in str(exc_info.value)

    # Pinned by override, no digest -> also refused.
    with pytest.raises(RuntimeError) as exc_info:
        jobs._resolve_cloud_image_source(_template(), {"image_url": "https://vendor.example/img.qcow2"})
    assert "image_url" in str(exc_info.value)

    # Template pin plus template digest resolves.
    assert jobs._resolve_cloud_image_source(
        _template(base_image_url="https://vendor.example/img.qcow2", base_image_sha256=digest),
        None,
    ) == ("https://vendor.example/img.qcow2", digest)

    # An override digest satisfies a template pin, and an override URL wins over both.
    assert jobs._resolve_cloud_image_source(
        _template(base_image_url="https://vendor.example/old.qcow2"),
        {"image_url": "https://vendor.example/new.qcow2", "image_sha256": digest},
    ) == ("https://vendor.example/new.qcow2", digest)

    # An uppercase digest is normalised rather than rejected: hex is case-insensitive,
    # and an operator pasting a vendor checksum verbatim should not break the build.
    assert jobs._resolve_cloud_image_source(
        _template(base_image_url="https://vendor.example/img.qcow2", base_image_sha256="B" * 64),
        None,
    ) == ("https://vendor.example/img.qcow2", digest)

    # A malformed digest is refused rather than forwarded to proxbox-api.
    for malformed in ("deadbeef", "g" * 64, "a" * 63, "a" * 65, "a" * 63 + "!"):
        with pytest.raises(RuntimeError):
            jobs._resolve_cloud_image_source(
                _template(base_image_url="https://vendor.example/img.qcow2", base_image_sha256=malformed),
                None,
            )

    # An UNPINNED release build still works without a digest: requiring one there would
    # break every existing template at once. That remaining gap is why pinning exists.
    url, sha = jobs._resolve_cloud_image_source(_template(), None)
    assert url.startswith("https://cloud-images.ubuntu.com/")
    assert sha == ""


@pytest.mark.parametrize(
    ("source", "image_url"),
    (
        ("override", "https://images.example/base.qcow2?token=do-not-persist"),
        (
            "template",
            (
                "https://images.example/base.qcow2?X-Amz-Credential=do-not-persist"
                "&X-Amz-Signature=do-not-persist"
            ),
        ),
    ),
)
def test_credentialed_base_image_urls_fail_at_the_job_boundary(
    monkeypatch,
    source,
    image_url,
) -> None:
    jobs = _load_jobs_isolated(monkeypatch)
    digest = "b" * 64
    template = _template(
        base_image_url=image_url if source == "template" else "",
        base_image_sha256=digest if source == "template" else "",
    )
    overrides = (
        {"image_url": image_url, "image_sha256": digest}
        if source == "override"
        else {}
    )

    with pytest.raises(RuntimeError, match="must not contain") as exc_info:
        jobs._resolve_cloud_image_source(template, overrides)
    assert "do-not-persist" not in str(exc_info.value)


def test_base_image_url_redaction_removes_all_credential_components() -> None:
    base_image = _load_base_image_module()
    tainted = (
        "https://operator:do-not-persist@images.example:8443/base.qcow2"
        "?token=do-not-persist#credential"
    )

    redacted = base_image.redact_base_image_url(tainted)

    assert redacted == "https://images.example:8443/base.qcow2"
    assert "operator" not in redacted
    assert "do-not-persist" not in redacted
    assert "token" not in redacted
    assert "credential" not in redacted


@pytest.mark.parametrize(
    "image_url",
    (
        "https://operator:do-not-persist@images.example/base.qcow2",
        "https://images.example/base.qcow2?token=do-not-persist",
        "https://images.example/base.qcow2#credential",
        "https://images.example/base.qcow2?",
        "https://images.example/base.qcow2#",
    ),
)
def test_base_image_url_validator_rejects_userinfo_query_and_fragment(image_url) -> None:
    base_image = _load_base_image_module()

    with pytest.raises(ValueError, match="must not contain"):
        base_image.validate_base_image_url(image_url)

    safe_url = "https://images.example/base.qcow2"
    assert base_image.validate_base_image_url(safe_url) == safe_url


def test_build_payload_forwards_the_digest_only_when_pinned(monkeypatch) -> None:
    """Plan and execute must carry the same digest; unpinned bodies omit it."""

    client = _load_proxbox_client()

    common = {
        "proxbox_api_url": "https://proxbox.example",
        "proxbox_api_key": "k",
        "name": "tpl",
        "vmid": 9052,
        "target_node": "node1",
        "image_url": "https://vendor.example/img.qcow2",
        "user_data_yaml": "#cloud-config\n",
        "endpoint_id": 17,
    }

    captured = _install_proxbox_responses(
        monkeypatch,
        client,
        [_plan_response(), _preflight_response(), _executed_response()],
    )
    client.call_proxbox_build(**common, image_sha256="c" * 64)
    assert len(captured) == 3
    plan_body, _, execute_body = [call["body"] for call in captured]
    assert plan_body["sha256"] == "c" * 64
    assert execute_body["sha256"] == "c" * 64

    captured = _install_proxbox_responses(
        monkeypatch,
        client,
        [_plan_response(), _preflight_response(), _executed_response()],
    )
    client.call_proxbox_build(**common)
    assert len(captured) == 3
    plan_body, _, execute_body = [call["body"] for call in captured]
    # Omitted from both recipe-defining bodies, so plan and execute cannot drift.
    assert "sha256" not in plan_body
    assert "sha256" not in execute_body


@pytest.mark.parametrize(
    (
        "template_pin",
        "build_pin",
        "current_pin",
        "expected_sha256",
        "expected_status",
        "bypass_url_validation",
    ),
    [
        (
            {"base_image_url": "https://vendor.example/pinned.qcow2", "base_image_sha256": "d" * 64},
            {},
            {},
            "d" * 64,
            "ready",
            False,
        ),
        ({}, {}, {}, "", "ready", False),
        (
            {"base_image_url": "https://vendor.example/pinned.qcow2", "base_image_sha256": "d" * 64},
            {"image_url": "https://vendor.example/override.qcow2", "image_sha256": "c" * 64},
            {},
            "c" * 64,
            "stale",
            False,
        ),
        (
            {"base_image_url": "https://vendor.example/pinned.qcow2", "base_image_sha256": "d" * 64},
            {},
            {"base_image_url": "https://vendor.example/edited.qcow2", "base_image_sha256": "c" * 64},
            "d" * 64,
            "stale",
            False,
        ),
        (
            {},
            {
                "image_url": (
                    "https://operator:do-not-persist@vendor.example/override.qcow2"
                    "?token=do-not-persist#credential"
                ),
                "image_sha256": "c" * 64,
            },
            {},
            "c" * 64,
            "stale",
            True,
        ),
    ],
)
def test_cloud_build_job_passes_and_snapshots_resolved_base_image(
    monkeypatch,
    template_pin,
    build_pin,
    current_pin,
    expected_sha256,
    expected_status,
    bypass_url_validation,
) -> None:
    """Protect the job-to-client seam and the atomic provenance snapshots."""

    jobs = _load_jobs_isolated(monkeypatch)
    if bypass_url_validation:
        # Simulate the structural rejection guard being bypassed so this test
        # independently proves the persistence/logging redaction backstop.
        def allow_tainted_url(value, **_kwargs):
            return value

        jobs.validate_base_image_url = allow_tainted_url
    finished_at = object()
    jobs.timezone.now = lambda: finished_at
    jobs._inject_monitoring_agents = Mock(return_value="#cloud-config\n")

    atomic_state = {"active": False}

    @contextmanager
    def atomic():
        assert not atomic_state["active"]
        atomic_state["active"] = True
        try:
            yield
        finally:
            atomic_state["active"] = False

    django_db = ModuleType("django.db")
    django_db.transaction = SimpleNamespace(atomic=atomic)
    monkeypatch.setitem(sys.modules, "django.db", django_db)

    template_manager = Mock()
    template_manager.filter.return_value = template_manager
    template_updates = []

    def record_template_update(**values):
        assert atomic_state["active"], "template provenance must be written inside transaction.atomic()"
        template_updates.append(values)

    template_manager.update.side_effect = record_template_update

    settings_row = SimpleNamespace(
        proxbox_api_url="https://proxbox.example",
        get_fileserver_package_read_token=lambda: "",
        get_proxbox_api_key=lambda: "api-key",
    )
    models_module = ModuleType("netbox_packer.models")
    models_module.PackerPluginSettings = type(
        "PackerPluginSettings",
        (),
        {"get_solo": staticmethod(lambda: settings_row)},
    )
    models_module.PackerTemplate = type("PackerTemplate", (), {"objects": template_manager})
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models_module)

    proxbox_response = {"status": "completed", "vmid": 9052}
    if bypass_url_validation:
        proxbox_response["stdout"] = f"downloaded {build_pin['image_url']}"
    call_proxbox_build = Mock(return_value=proxbox_response)
    client_module = ModuleType("netbox_packer.proxbox_client")
    client_module.ProxboxApiError = type("ProxboxApiError", (Exception,), {})
    client_module.call_proxbox_build = call_proxbox_build
    monkeypatch.setitem(sys.modules, "netbox_packer.proxbox_client", client_module)

    installer = SimpleNamespace(
        content="#cloud-config\n",
        installer_type="cloud_config",
        checksum="e" * 64,
    )
    template_fields = {
        "pk": 44,
        "name": "base-image-test",
        "installer_config": installer,
        "storage_pool": "local",
        "proxmox_node": "node1",
        "proxmox_endpoint": "",
        "proxmox_template_id": 9052,
        "os_family": "ubuntu",
        "os_version": "24.04",
        "base_image_url": "",
        "base_image_sha256": "",
        "is_fileserver_golden_template": False,
        "install_qemu_guest_agent": False,
        "install_zabbix_agent2": False,
        "zabbix_server": "",
        "install_nms_agent": False,
    }
    template_fields.update(template_pin)
    template = SimpleNamespace(**template_fields)
    locked_template_fields = {
        "pk": template.pk,
        "base_image_url": template.base_image_url,
        "base_image_sha256": template.base_image_sha256,
    }
    locked_template_fields.update(current_pin)
    locked_template = SimpleNamespace(**locked_template_fields)

    def get_locked_template(*, pk):
        assert atomic_state["active"], "template pin must be reloaded under transaction.atomic()"
        assert pk == template.pk
        return locked_template

    template_manager.select_for_update.return_value = template_manager
    template_manager.get.side_effect = get_locked_template

    saved_update_fields = []

    def save_build(*, update_fields):
        assert atomic_state["active"], "build provenance must be written inside transaction.atomic()"
        saved_update_fields.append(update_fields)

    build = SimpleNamespace(
        variable_overrides={
            "endpoint_id": 17,
            "target_node": "node1",
            **build_pin,
        },
        result_template_id=None,
        log="",
        save=save_build,
    )

    jobs.PackerBuildJob()._run_proxbox_cloud_build(build, template, "node1", 60)

    client_kwargs = call_proxbox_build.call_args.kwargs
    expected_url = build_pin.get("image_url") or template_pin.get(
        "base_image_url",
        "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img",
    )
    safe_expected_url = jobs.redact_base_image_url(expected_url)
    assert client_kwargs["image_url"] == expected_url
    assert client_kwargs["image_sha256"] == expected_sha256
    assert build.base_image_url_at_build == safe_expected_url
    assert build.base_image_sha256_at_build == expected_sha256
    assert template_updates == [
        {
            "build_status": expected_status,
            "built_at": finished_at,
            "base_image_url_at_build": safe_expected_url,
            "base_image_sha256_at_build": expected_sha256,
            "installer_config_checksum_at_build": "e" * 64,
        }
    ]
    assert "base_image_url_at_build" in saved_update_fields[0]
    assert "base_image_sha256_at_build" in saved_update_fields[0]
    template_manager.select_for_update.assert_called_once_with()
    template_manager.get.assert_called_once_with(pk=template.pk)
    if bypass_url_validation:
        assert "do-not-persist" not in build.log
        assert safe_expected_url in build.log
    assert not atomic_state["active"]


@pytest.mark.parametrize(
    ("desired_url", "desired_sha256", "built_url", "built_sha256", "expected"),
    [
        ("", "", "", "", False),
        ("", "", "https://vendor.example/latest.qcow2", "", False),
        ("https://vendor.example/a.qcow2", "a" * 64, "https://vendor.example/a.qcow2", "a" * 64, False),
        ("https://vendor.example/b.qcow2", "a" * 64, "https://vendor.example/a.qcow2", "a" * 64, True),
        ("https://vendor.example/a.qcow2", "b" * 64, "https://vendor.example/a.qcow2", "a" * 64, True),
        ("", "", "https://vendor.example/override.qcow2", "a" * 64, True),
        ("https://vendor.example/a.qcow2", "a" * 64, "", "", True),
        ("https://vendor.example/a.qcow2", "A" * 64, "https://vendor.example/a.qcow2", "a" * 64, False),
    ],
)
def test_base_image_pin_staleness(
    desired_url,
    desired_sha256,
    built_url,
    built_sha256,
    expected,
) -> None:
    helper = _load_base_image_module()

    assert (
        helper.pin_differs_from_built_source(
            desired_url=desired_url,
            desired_sha256=desired_sha256,
            built_url=built_url,
            built_sha256=built_sha256,
        )
        is expected
    )


def test_staleness_evaluates_pin_drift_without_an_age_policy() -> None:
    models_src = _read("netbox_packer/models.py")
    jobs_src = _read("netbox_packer/jobs.py")

    assert "pin_differs_from_built_source(" in models_src
    assert "return age_stale or config_stale or base_image_stale" in models_src
    staleness_job = jobs_src.split("class PackerStalenessCheckJob", 1)[1].split("def dispatch_build", 1)[0]
    assert '.exclude(max_age_days=None)' not in staleness_job

    # A pin only counts as drift where the builder actually enforces it. The local Packer
    # path records no at-build snapshot, so honouring a pin there would report the
    # template stale forever (desired set, built permanently empty) and `auto_rebuild`
    # would turn that into an endless rebuild loop. The decision lives in the Django-free
    # helper so it can be exercised behaviourally rather than matched as source text.
    from netbox_packer.base_image import base_image_pin_applies

    assert base_image_pin_applies("cloud_config") is True
    for installer_type in ("autoinstall", "kickstart", "preseed", "", None):
        assert base_image_pin_applies(installer_type) is False, installer_type

    # `is_stale` must actually consult it, and `clean()` must refuse to create the state.
    assert "self.supports_base_image_pin and pin_differs_from_built_source(" in models_src
    assert "base_image_pin_applies(installer.installer_type)" in models_src
    template_clean = models_src.split("    def clean(self):", 1)[1].split("\n    @property", 1)[0]
    assert "supports_base_image_pin" in template_clean
    assert "ValidationError" in template_clean


def test_base_image_pin_fields_are_exposed_and_migrated() -> None:
    """A field nobody can set is not a feature."""

    models_src = _read("netbox_packer/models.py")
    assert "base_image_url = models.URLField(" in models_src
    assert "base_image_sha256 = models.CharField(" in models_src
    assert r"^[0-9a-f]{64}$" in models_src

    migration = _read("netbox_packer/migrations/0027_packertemplate_base_image_pin.py")
    for field in ("base_image_url", "base_image_sha256"):
        assert f'name="{field}"' in migration, field
    # Depends on 0026, not 0025: both this branch and the 0020 hardening originally
    # numbered themselves 0026, which would have left two migration leaves.
    assert '("netbox_packer", "0026_harden_influxdb_0020_profiles")' in migration

    forms_src = _read("netbox_packer/forms.py")
    serializer_src = _read("netbox_packer/api/serializers.py")
    for field in ("base_image_url", "base_image_sha256"):
        assert f'"{field}"' in forms_src, ("forms", field)
        assert f'"{field}"' in serializer_src, ("serializer", field)


def test_base_image_build_snapshots_are_machine_managed_and_migration_graph_is_linear() -> None:
    snapshot_fields = ("base_image_url_at_build", "base_image_sha256_at_build")
    models_src = _read("netbox_packer/models.py")
    forms_src = _read("netbox_packer/forms.py")
    serializers_src = _read("netbox_packer/api/serializers.py")
    template_form = forms_src.split("class PackerTemplateForm", 1)[1].split("\nclass ", 1)[0]
    template_serializer = serializers_src.split("class PackerTemplateSerializer", 1)[1].split("\nclass ", 1)[0]
    build_serializer = serializers_src.split("class PackerBuildSerializer", 1)[1].split("\nclass ", 1)[0]

    for field in snapshot_fields:
        assert models_src.count(f"{field} = models.") == 2
        assert f'"{field}"' not in template_form
        assert f'"{field}"' in template_serializer
        assert f'"{field}"' in build_serializer
        assert f'"{field}"' in template_serializer.split("read_only_fields =", 1)[1]
        assert f'"{field}"' in build_serializer.split("read_only_fields =", 1)[1]

    migration_path = PKG / "migrations" / "0028_base_image_build_snapshots.py"
    migration = migration_path.read_text(encoding="utf-8")
    assert '("netbox_packer", "0027_packertemplate_base_image_pin")' in migration
    for model_name in ("packerbuild", "packertemplate"):
        for field in snapshot_fields:
            assert f'model_name="{model_name}",' in migration
            assert f'name="{field}",' in migration

    migration_paths = sorted((PKG / "migrations").glob("[0-9][0-9][0-9][0-9]_*.py"))
    names = {path.stem for path in migration_paths}
    numbers = [path.stem.split("_", 1)[0] for path in migration_paths]
    assert len(numbers) == len(set(numbers)), "netbox-packer has duplicate migration numbers"

    internal_dependencies = set()
    for path in migration_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        migration_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
        )
        dependencies_node = next(
            node.value
            for node in migration_class.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "dependencies" for target in node.targets)
        )
        for app_label, dependency in ast.literal_eval(dependencies_node):
            if app_label == "netbox_packer":
                internal_dependencies.add(dependency)

    assert names - internal_dependencies == {"0030_seed_influxdb3_explorer_debian13_cloud_init"}


def test_influxdb3_debian13_base_image_pin_is_dated_and_verifiable() -> None:
    """The one pinned profile must name a dated artifact and a well-formed digest.

    Migrations 0027/0028 built the pin machinery; 0029 is the only place it is actually
    used. The value of the whole feature collapses if this pin silently points back at a
    mutable directory, so assert the shape here rather than trusting review.
    """
    migration = (PKG / "migrations" / "0029_pin_influxdb3_debian13_base_image.py").read_text(
        encoding="utf-8"
    )
    namespace: dict[str, object] = {}
    tree = ast.parse(migration)
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                namespace[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue

    url = namespace["PINNED_IMAGE_URL"]
    digest = namespace["PINNED_IMAGE_SHA256"]
    assert isinstance(url, str) and isinstance(digest, str)

    # A pin whose URL still resolves through `latest/` pins nothing: Debian rewrites
    # that directory on every publish, so the digest would start failing verification
    # the moment a new snapshot lands.
    assert "/latest/" not in url
    assert re.search(r"/images/cloud/trixie/\d{8}-\d+/", url), url
    assert url.endswith(".qcow2")
    assert "genericcloud-amd64" in url, "pin must match the artifact the resolver derives"
    assert url.startswith("https://")

    assert re.fullmatch(r"[0-9a-f]{64}", digest), "digest must be 64 lowercase hex chars"

    assert namespace["TEMPLATE_NAME"] == "influxdb-core-3.11.0-debian-13"
    assert '("netbox_packer", "0028_base_image_build_snapshots")' in migration

    # The write must not clobber an operator's own pin, and rollback must not restore
    # the unverified mutable base.
    assert "select_for_update()" in migration
    assert 'base_image_url="", base_image_sha256=""' in migration
    assert "Refusing to overwrite an existing base image pin" in migration
    assert "Intentionally a no-op" in migration


def test_influxdb3_core_debian13_contract_is_documented() -> None:
    required = (
        "influxdb-core-3.11.0-debian-13",
        "9052",
        "Debian 13",
        "influxdb3-core.service",
        "service.influxdb.1.bootstrap",
    )
    for rel in (
        "README.md",
        "CLAUDE.md",
        "AGENTS.md",
        "docs/cloud-init-template-images.md",
        "docs/index.md",
    ):
        doc = _read(rel)
        for text in required:
            assert text in doc, f"{rel} must document {text}"


def test_influxdb3_explorer_debian13_contract_is_documented() -> None:
    required = (
        "influxdb3-explorer-1.9.0-debian-13",
        "9053",
        "influxdata/influxdb3-ui",
        "sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc",
        "influxdb3-explorer.service",
        "service.influxdb.1.token_create",
        "nms-secret:<opaque-id>",
    )
    for rel in (
        "README.md",
        "CLAUDE.md",
        "AGENTS.md",
        "docs/cloud-init-template-images.md",
        "docs/index.md",
    ):
        doc = _read(rel)
        for text in required:
            assert text in doc, f"{rel} must document {text}"


def test_ubuntu_lts_base_seed_contract() -> None:
    rel = "netbox_packer/migrations/0016_seed_ubuntu_lts_base_cloud_init.py"
    src = _read(rel)
    constants = _literal_assignments(rel)

    assert constants["CONFIG_NAME"] == "ubuntu-lts-base-cloud-config"
    assert constants["CONFIG_VERSION"] == "1.0.0"
    assert constants["PROXMOX_ENDPOINT"] == "https://10.0.30.71:8006"
    assert constants["PROXMOX_NODE"] == "10.0.30.71"
    # Minimal base config; agents + ssh_pwauth are injected at build time.
    assert constants["UBUNTU_LTS_BASE_CLOUD_CONFIG"].startswith("#cloud-config\n")

    # The three most recent Ubuntu LTS releases on fresh, unused VMIDs.
    assert '("ubuntu-2204-cloudinit-base", "22.04", 9040)' in src
    assert '("ubuntu-2404-cloudinit-base", "24.04", 9041)' in src
    assert '("ubuntu-2604-cloudinit-base", "26.04", 9042)' in src

    assert '"installer_type": "cloud_config"' in src
    assert '"storage_pool": "local"' in src
    assert '"cloud_init_ready": True' in src
    assert '"install_qemu_guest_agent": True' in src
    assert '"install_zabbix_agent2": True' in src
    assert '"zabbix_server": "zabbix.nmulti.cloud"' in src
    # Idempotent + reversible seed (deletes only its own named rows on reverse).
    assert "get_or_create" in src
    assert "def unseed_ubuntu_lts_base" in src
    assert '("netbox_packer", "0015_seed_passbolt_cloud_init")' in src


def test_serializer_exposes_monitoring_agent_fields() -> None:
    src = _read("netbox_packer/api/serializers.py")
    assert '"install_qemu_guest_agent"' in src
    assert '"install_zabbix_agent2"' in src
    assert '"zabbix_server"' in src
    assert '"install_nms_agent"' in src
    assert '"nms_agent_backend_url"' in src
    assert '"provisions_service"' in src
    template_serializer = src.split("class PackerTemplateSerializer", 1)[1].split("\nclass ", 1)[0]
    assert '"provisions_service"' in template_serializer.split("read_only_fields =", 1)[1]

    filter_src = _read("netbox_packer/filtersets.py")
    assert '"provisions_service"' in filter_src
