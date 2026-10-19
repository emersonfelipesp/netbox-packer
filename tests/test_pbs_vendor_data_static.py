import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SEED = ROOT / "netbox_packer/seeds/pbs-4-debian-13.vendor-data.yaml"
MIGRATION = ROOT / "netbox_packer/migrations/0036_seed_pbs4_debian13_vendor_data.py"
KEY_SHA256 = "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45"


def _load_migration():
    django = types.ModuleType("django")
    django_db = types.ModuleType("django.db")
    django_db.migrations = SimpleNamespace(
        Migration=object,
        RunPython=lambda forward, reverse: (forward, reverse),
    )
    recorder = types.ModuleType("django.db.migrations.recorder")
    recorder.MigrationRecorder = object
    legacy_columns = types.ModuleType("netbox_packer.migrations._legacy_agent_columns")
    legacy_columns.ensure_legacy_column_defaults = lambda *_args: None
    sys.modules.setdefault("django", django)
    sys.modules.setdefault("django.db", django_db)
    sys.modules.setdefault("django.db.migrations.recorder", recorder)
    sys.modules.setdefault("netbox_packer.migrations._legacy_agent_columns", legacy_columns)
    spec = importlib.util.spec_from_file_location("pbs_seed", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pbs_vendor_data_contract() -> None:
    source = SEED.read_text()
    document = yaml.safe_load(source)
    assert source.startswith("#cloud-config\n")
    assert document["disable_root"] is False
    for forbidden in ("users", "chpasswd", "ssh_authorized_keys", "hostname"):
        assert forbidden not in document
    assert document["runcmd"] == [["/usr/local/sbin/install-proxmox-backup-server"]]
    installer = document["write_files"][0]
    assert installer["permissions"] == "0700"
    script = installer["content"]
    assert "set -eu\n" in script
    assert "set -o pipefail" in script
    assert KEY_SHA256 in script
    assert "sha256sum --check --strict" in script
    source_block = """Types: deb
URIs: http://download.proxmox.com/debian/pbs
Suites: trixie
Components: pbs-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg"""
    assert source_block in script
    assert "DEBIAN_FRONTEND=noninteractive" in script
    assert "apt-get install -y proxmox-backup-server qemu-guest-agent" in script
    assert "SECONDS + 120" in script
    # The package ships an enabled enterprise repository; the installer must disable
    # it and prove that apt still works before it records completion.
    disable = script.index("pbs-enterprise.sources")
    assert 'mv -f "${enterprise}" "${enterprise}.disabled"' in script[disable:]
    commands = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in commands if "apt-get update" in line]
    assert script.count("apt-get --error-on=any update") == 2
    first_update = script.index("apt-get --error-on=any update")
    install = script.index("apt-get install")
    last_update = script.rindex("apt-get --error-on=any update")
    assert disable < first_update < install < last_update
    assert script.index("NETBOX_PBS_INSTALL_COMPLETE") > last_update
    lowered = source.lower()
    for secret in ("password:", "token:", "private key", "ssh_authorized_keys"):
        assert secret not in lowered


def test_migration_embeds_seed_verbatim_and_uses_free_vmid() -> None:
    migration = _load_migration()
    assert SEED.read_text() == migration.VENDOR_DATA
    assert migration.TEMPLATE_VMID == 9061


def test_vendor_data_choice_and_validation_are_declared() -> None:
    source = (ROOT / "netbox_packer/models.py").read_text()
    assert '("vendor_data", "Cloud-init vendor-data (keeps per-VM Cloud-Init fields)")' in source
    assert 'if self.installer_type != "vendor_data":' in source
    assert 'content.startswith("#!")' in source
    assert 'content.startswith("#cloud-config")' in source
    assert "yaml.safe_load(content)" in source


class _Manager:
    def __init__(self, model):
        self.model = model
        self.rows = {}

    def get_or_create(self, *, name, defaults, version=None):
        key = (name, version)
        if key in self.rows:
            return self.rows[key], False
        row = self.model(name=name, version=version, pk=len(self.rows) + 1, **defaults)
        if "installer_config" in defaults:
            row.installer_config_id = defaults["installer_config"].pk
        self.rows[key] = row
        return row, True


def test_seed_is_idempotent_and_collision_guarded() -> None:
    migration = _load_migration()
    migration.MigrationRecorder = lambda _connection: SimpleNamespace(applied_migrations=lambda: {})
    schema_editor = SimpleNamespace(connection=SimpleNamespace(vendor="sqlite"))

    class Installer(SimpleNamespace):
        pass

    class Template(SimpleNamespace):
        pass

    Installer.objects = _Manager(Installer)
    Template.objects = _Manager(Template)
    apps = SimpleNamespace(get_model=lambda _app, name: Installer if name == "PackerInstallerConfig" else Template)
    migration.seed_pbs4_debian13(apps, schema_editor)
    migration.seed_pbs4_debian13(apps, schema_editor)
    template = Template.objects.rows[(migration.TEMPLATE_NAME, None)]
    template.os_version = "12"
    with pytest.raises(RuntimeError, match="naming collision"):
        migration.seed_pbs4_debian13(apps, schema_editor)
    assert migration.unseed_pbs4_debian13(apps, None) is None


def test_old_pbs_migration_record_adopts_operator_edits_without_query_or_write() -> None:
    migration = _load_migration()
    events: list[str] = []

    class Recorder:
        def __init__(self, connection) -> None:
            del connection
            events.append("recorder")

        def applied_migrations(self):
            return {("netbox_packer", "0035_seed_pbs4_debian13_vendor_data"): object()}

    migration.MigrationRecorder = Recorder
    migration.ensure_legacy_column_defaults = lambda *_args: events.append("legacy-defaults")
    apps = SimpleNamespace(
        get_model=lambda *_args: (_ for _ in ()).throw(AssertionError("adoption must not query seeded rows"))
    )

    migration.seed_pbs4_debian13(
        apps,
        SimpleNamespace(connection=SimpleNamespace(vendor="sqlite")),
    )

    assert events == ["legacy-defaults", "recorder"]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_data_client_payload_is_bound_across_signed_handshake(monkeypatch) -> None:
    client = _load_module("pbs_vendor_proxbox_client", ROOT / "netbox_packer/proxbox_client.py")
    calls: list[tuple[str, dict]] = []

    def post_json(*, path: str, payload: dict, **_kwargs) -> dict:
        calls.append((path, payload))
        if path.endswith("/preflight"):
            return {
                "ready": True,
                "writes_enabled": True,
                "recipe_digest": "a" * 64,
                "plan_token": "signed-token",
                "expires_at": 4_000_000_000.0,
            }
        if payload["execute"] is False:
            return {"recipe_digest": "a" * 64}
        return {
            "status": "completed",
            "verified": True,
            "execution_enabled": True,
            "execution": {"attempted": True, "enabled": True, "exit_code": 0},
        }

    monkeypatch.setattr(client, "_post_json", post_json)
    script = "#cloud-config\ndisable_root: false\n"
    client.call_proxbox_build(
        proxbox_api_url="https://proxbox.example",
        proxbox_api_key="api-key",
        name="pbs-4-debian-13",
        vmid=9061,
        target_node="pve1",
        image_url="https://cloud.debian.org/debian-13.qcow2",
        user_data_yaml=None,
        first_boot_script=script,
        endpoint_id=17,
    )

    plan, preflight, execute = [payload for _path, payload in calls]
    assert plan["first_boot_script"] == script
    assert execute["first_boot_script"] == script
    assert "user_data_yaml" not in plan
    assert "user_data_yaml" not in execute
    assert "first_boot_script" not in preflight
    assert preflight["recipe_digest"] == "a" * 64
    assert execute["preflight_plan_token"] == "signed-token"


def test_cloud_config_client_payload_is_unchanged(monkeypatch) -> None:
    client = _load_module("pbs_cloud_proxbox_client", ROOT / "netbox_packer/proxbox_client.py")
    captured = {}
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda **kwargs: captured.setdefault("payload", kwargs["payload"]),
    )
    content = "#cloud-config\n"
    assert (
        client.call_proxbox_build(
            proxbox_api_url="https://proxbox.example",
            proxbox_api_key="api-key",
            name="existing",
            vmid=9000,
            target_node=None,
            image_url="https://example.invalid/image.qcow2",
            user_data_yaml=content,
            execute=False,
        )
        == captured["payload"]
    )
    assert captured["payload"]["user_data_yaml"] == content
    assert "first_boot_script" not in captured["payload"]


def test_client_refuses_both_cloud_payload_fields() -> None:
    client = _load_module("pbs_conflict_proxbox_client", ROOT / "netbox_packer/proxbox_client.py")
    with pytest.raises(ValueError, match="mutually exclusive"):
        client.call_proxbox_build(
            proxbox_api_url="https://proxbox.example",
            proxbox_api_key="api-key",
            name="conflict",
            vmid=9061,
            target_node=None,
            image_url="https://example.invalid/image.qcow2",
            user_data_yaml="#cloud-config\n",
            first_boot_script="#!/bin/sh\n",
            execute=False,
        )


def test_client_surfaces_proxbox_conflict_code_without_payload(monkeypatch) -> None:
    client = _load_module("pbs_422_proxbox_client", ROOT / "netbox_packer/proxbox_client.py")
    secret = "must-not-appear"
    response = json.dumps(
        {
            "detail": {
                "code": "first_boot_script_user_data_conflict",
                "message": "The cloud payload fields are mutually exclusive.",
            }
        }
    ).encode()

    def reject(req, timeout=0, verify_ssl=True):
        del verify_ssl
        raise client.urllib.error.HTTPError(req.full_url, 422, "Unprocessable", {}, io.BytesIO(response))

    monkeypatch.setattr(client, "_open_request", reject)
    with pytest.raises(client.ProxboxApiError) as caught:
        client.call_proxbox_build(
            proxbox_api_url="https://proxbox.example",
            proxbox_api_key="api-key",
            name="pbs",
            vmid=9061,
            target_node=None,
            image_url="https://example.invalid/image.qcow2",
            user_data_yaml=None,
            first_boot_script=f"#!/bin/sh\n# {secret}\n",
            execute=False,
        )
    assert caught.value.error_code == "first_boot_script_user_data_conflict"
    assert "first_boot_script_user_data_conflict" in str(caught.value)
    assert secret not in str(caught.value)


def test_client_without_cloud_payload_keeps_catalog_behavior(monkeypatch) -> None:
    client = _load_module("pbs_empty_proxbox_client", ROOT / "netbox_packer/proxbox_client.py")
    captured = {}
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda **kwargs: captured.setdefault("payload", kwargs["payload"]),
    )
    client.call_proxbox_build(
        proxbox_api_url="https://proxbox.example",
        proxbox_api_key="api-key",
        name="catalog",
        vmid=9061,
        target_node=None,
        image_url="https://example.invalid/image.qcow2",
        user_data_yaml=None,
        execute=False,
    )
    assert "user_data_yaml" not in captured["payload"]
    assert "first_boot_script" not in captured["payload"]
