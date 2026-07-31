"""Tests for the cloud-init template image build path.

Static (text/AST) assertions plus an isolated functional test of
``proxbox_client`` — all run without Django or NetBox installed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import traceback
from pathlib import Path

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
    assert "user_data_yaml = render_fileserver_package_index(" in src
    assert "template_name=template.name," in src
    assert "is_fileserver_golden_template=template.is_fileserver_golden_template," in src
    assert "redact_fileserver_package_token(str(value))" in src
    assert "raise sanitized_fileserver_package_error(exc) from None" in src
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

    upgrade_constants = _literal_assignments("netbox_packer/migrations/0017_update_fileserver_agent_package_index.py")
    assert upgrade_constants["FILESERVER_ALLINONE_CLOUD_CONFIG"] == seed
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
        "NMS_FILESERVER_PACKAGE_READ_USER",
        "NMS_FILESERVER_PACKAGE_READ_TOKEN",
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


def test_fileserver_package_index_upgrade_migration_contract() -> None:
    rel = "netbox_packer/migrations/0017_update_fileserver_agent_package_index.py"
    src = _read(rel)
    constants = _literal_assignments(rel)

    assert constants["CONFIG_NAME"] == "fileserver-allinone-cloud-config"
    assert constants["PREVIOUS_CONFIG_VERSION"] == "1.0.0"
    assert constants["CONFIG_VERSION"] == "1.0.1"
    assert constants["TEMPLATE_NAME"] == "tpl-fileserver-allinone-ubuntu-2404"
    assert constants["TEMPLATE_VMID"] == 9300
    seed = _read("netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml")
    assert constants["FILESERVER_ALLINONE_CLOUD_CONFIG"] == seed
    assert "PackerInstallerConfig.objects.update_or_create(" in src
    assert "proxmox_template_id=TEMPLATE_VMID" in src
    # The template's own description must be corrected alongside proxmox_template_id —
    # otherwise it keeps advertising the superseded VMID 9032 after this migration runs.
    assert "VMID 9300" in src
    assert "VMID 9032" not in src.split("def update_fileserver_package_index", 1)[1].split("def ", 1)[0]
    assert 'update(installer_config=previous, build_status="pending")' in src
    assert "PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()" in src
    assert '("netbox_packer", "0016_seed_ubuntu_lts_base_cloud_init")' in src


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


def test_fileserver_package_index_authorizes_by_flag_not_name(monkeypatch) -> None:
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
            config, template_name=mod.FILESERVER_TEMPLATE_NAME, is_fileserver_golden_template=False
        )
    except RuntimeError as exc:
        assert "not the File Server golden template" in str(exc)
    else:  # pragma: no cover - name alone must never authorize credential injection
        raise AssertionError("expected a reclaimed name with flag=False to be rejected")

    # The original golden template, renamed away, must keep working because the
    # flag (not the name) is what carries trust.
    monkeypatch.setenv(mod.PACKAGE_READ_USER_ENV, "fileserver reader")
    monkeypatch.setenv(mod.PACKAGE_READ_TOKEN_ENV, "token")
    rendered = mod.render_fileserver_package_index(
        config, template_name="renamed-golden-template", is_fileserver_golden_template=True
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


def test_fileserver_package_index_credentials_are_required_and_url_encoded(monkeypatch) -> None:
    mod = _load_package_index()
    config = (
        "index-url = https://"
        + mod.PACKAGE_READ_USER_PLACEHOLDER
        + ":"
        + mod.PACKAGE_READ_TOKEN_PLACEHOLDER
        + "@git.nmulti.cloud/api/packages/N-MultiCloud/pypi/simple/"
    )

    monkeypatch.delenv(mod.PACKAGE_READ_USER_ENV, raising=False)
    monkeypatch.delenv(mod.PACKAGE_READ_TOKEN_ENV, raising=False)
    try:
        mod.render_fileserver_package_index(
            config, template_name=mod.FILESERVER_TEMPLATE_NAME, is_fileserver_golden_template=True
        )
    except RuntimeError as exc:
        assert mod.PACKAGE_READ_USER_ENV in str(exc)
        assert mod.PACKAGE_READ_TOKEN_ENV in str(exc)
    else:  # pragma: no cover - a credentialed bake must fail closed
        raise AssertionError("expected missing package-index credentials to fail")

    monkeypatch.setenv(mod.PACKAGE_READ_USER_ENV, "fileserver reader")
    monkeypatch.setenv(mod.PACKAGE_READ_TOKEN_ENV, "read/token?only")
    rendered = mod.render_fileserver_package_index(
        config, template_name=mod.FILESERVER_TEMPLATE_NAME, is_fileserver_golden_template=True
    )
    assert "fileserver%20reader:read%2Ftoken%3Fonly@" in rendered
    assert "read/token?only" not in rendered


def test_fileserver_package_index_rejects_placeholders_on_other_templates(monkeypatch) -> None:
    """An unrelated template embedding the placeholder strings must never receive credentials."""
    mod = _load_package_index()
    config = (
        "index-url = https://"
        + mod.PACKAGE_READ_USER_PLACEHOLDER
        + ":"
        + mod.PACKAGE_READ_TOKEN_PLACEHOLDER
        + "@git.nmulti.cloud/api/packages/N-MultiCloud/pypi/simple/"
    )
    monkeypatch.setenv(mod.PACKAGE_READ_USER_ENV, "fileserver reader")
    monkeypatch.setenv(mod.PACKAGE_READ_TOKEN_ENV, "read/token?only")

    try:
        mod.render_fileserver_package_index(
            config, template_name="some-other-template", is_fileserver_golden_template=False
        )
    except RuntimeError as exc:
        assert "not the File Server golden template" in str(exc)
    else:  # pragma: no cover - credential injection must be scoped to the File Server template
        raise AssertionError("expected placeholders on an unrelated template to be rejected")


def test_fileserver_package_index_non_target_passthrough_and_log_redaction(monkeypatch) -> None:
    mod = _load_package_index()
    unrelated = "#cloud-config\npackages:\n  - qemu-guest-agent\n"
    assert (
        mod.render_fileserver_package_index(
            unrelated, template_name="some-other-template", is_fileserver_golden_template=False
        )
        == unrelated
    )
    assert (
        mod.render_fileserver_package_index(
            unrelated, template_name=mod.FILESERVER_TEMPLATE_NAME, is_fileserver_golden_template=True
        )
        == unrelated
    )

    monkeypatch.setenv(mod.PACKAGE_READ_TOKEN_ENV, "read/token?only")
    output = "raw=read/token?only encoded=read%2Ftoken%3Fonly"
    redacted = mod.redact_fileserver_package_token(output)
    assert "read/token?only" not in redacted
    assert "read%2Ftoken%3Fonly" not in redacted
    assert redacted.count(mod.REDACTED_PACKAGE_TOKEN) == 2

    try:
        raise RuntimeError(output)
    except RuntimeError as original:
        try:
            raise mod.sanitized_fileserver_package_error(original) from None
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
            template_name=mod.FILESERVER_TEMPLATE_NAME,
            is_fileserver_golden_template=True,
        )
    except RuntimeError as exc:
        assert "placeholders are missing" in str(exc)
    else:  # pragma: no cover - the target config must never fall back to public PyPI
        raise AssertionError("expected missing File Server placeholders to fail")


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
    # Password SSH: every baked image permits password auth (ssh_pwauth), unless
    # the template already declares it. The password itself is never baked.
    assert '"ssh_pwauth" not in config' in src
    assert 'config["ssh_pwauth"] = True' in src


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
