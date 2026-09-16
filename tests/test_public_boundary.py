"""Regression tests for the public-repository boundary."""

import hashlib
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_scanner():
    path = ROOT / "scripts" / "check_public_boundary.py"
    spec = importlib.util.spec_from_file_location("check_public_boundary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_upgrade_probe():
    path = ROOT / "scripts" / "check_historical_upgrade.py"
    spec = importlib.util.spec_from_file_location("check_historical_upgrade", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_boundary_scanner_rejects_content_and_path_mutations() -> None:
    scanner = _load_scanner()
    token = b"qvz"
    digest = hashlib.sha256(token).hexdigest()
    mutations = (
        b"prefix" + token.upper() + b"suffix",
        token[:1] + b'" + "' + token[1:],
        token[:1] + b"\n" + token[1:],
        token[:1] + b"-" + token[1:],
        token[:1] + (b"-" * 64) + token[1:],
        b"".join(f"\\x{value:02x}".encode() for value in token),
        b"".join(f"\\u{value:04x}".encode() for value in token),
        b"".join(f"%{value:02x}".encode() for value in token),
        token.hex().encode(),
        b"".join(f"&#{value};".encode() for value in token),
        "".join(chr(value + 0xFEE0) for value in token.upper()).encode(),
        b"\xff" + token + b"\xfe",
        token[:1] + b"\0" + token[1:],
    )

    def scan(files):
        return scanner.find_violations(files, forbidden_digest=digest, forbidden_length=len(token))

    assert scan({"README.md": b"Standalone image builds."}) == []
    for mutation in mutations:
        assert scan({"mutation.bin": mutation})

    split_path = f"private-{chr(token[0])}-{token[1:].decode()}"
    assert scan({split_path: b"clean"})


def test_boundary_scanner_reads_staged_blob_hidden_by_clean_worktree(tmp_path: Path, monkeypatch) -> None:
    scanner = _load_scanner()
    token = b"qvz"
    monkeypatch.setattr(scanner, "_FORBIDDEN_DIGEST", hashlib.sha256(token).hexdigest())
    _git(tmp_path, "init", "-q")
    target = tmp_path / "boundary.txt"
    target.write_bytes(b"clean\n")
    _git(tmp_path, "add", "boundary.txt")
    target.write_bytes(b"private-" + token + b"\n")
    _git(tmp_path, "add", "boundary.txt")
    target.write_bytes(b"clean again\n")

    assert scanner.find_violations(scanner.index_files(tmp_path), origin="index")
    assert scanner.find_violations(scanner.worktree_files(tmp_path), origin="worktree") == []
    violations = scanner.repository_violations(tmp_path, source="all")
    assert any(item.startswith("index:") for item in violations)


def test_boundary_scanner_scans_symlink_targets_without_following_them(tmp_path: Path, monkeypatch) -> None:
    scanner = _load_scanner()
    token = b"qvz"
    monkeypatch.setattr(scanner, "_FORBIDDEN_DIGEST", hashlib.sha256(token).hexdigest())
    _git(tmp_path, "init", "-q")
    link = tmp_path / "published-link"
    os.symlink(os.fsdecode(b"private-" + token), link)
    _git(tmp_path, "add", "published-link")

    assert scanner.find_violations(scanner.index_files(tmp_path), origin="index")
    assert scanner.find_violations(scanner.worktree_files(tmp_path), origin="worktree")


def test_boundary_scanner_fails_closed_outside_a_repository(tmp_path: Path) -> None:
    scanner = _load_scanner()
    with pytest.raises(RuntimeError, match="Git command failed"):
        scanner.index_files(tmp_path)


def test_historical_upgrade_probe_uses_typed_generic_sentinels() -> None:
    probe = _load_upgrade_probe()

    class Field:
        max_length = 12

        def __init__(self, field_type):
            self.field_type = field_type

        def get_internal_type(self):
            return self.field_type

    assert probe._sentinel_value(Field("BooleanField"), 1) is True
    assert probe._sentinel_value(Field("BinaryField"), 2) == b"legacy-binary-2"
    assert probe._sentinel_value(Field("JSONField"), 3) == {"legacy_sentinel": 3}
    assert probe._sentinel_value(Field("PositiveIntegerField"), 4) == 704
    assert probe._sentinel_value(Field("CharField"), 5) == "legacy-senti"
    with pytest.raises(RuntimeError, match="Unsupported legacy sentinel field type"):
        probe._sentinel_value(Field("ForeignKey"), 6)


def test_github_ci_runs_base_upgrade_and_separate_fresh_database() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "b2feff929698ba50145312fcfa265b1f49004b6c" in workflow
    assert "check_historical_upgrade.py capture-head" in workflow
    assert "check_historical_upgrade.py seed-base" in workflow
    assert "check_historical_upgrade.py assert-head" in workflow
    assert "NETBOX_TEST_DB_NAME: netbox_fresh" in workflow
    assert "migrate netbox_packer 0032" in workflow
    assert "migrate netbox_packer 0033" in workflow


def _load_migration_0033(monkeypatch):
    class RunPython:
        noop = staticmethod(lambda *_args, **_kwargs: None)

        def __init__(self, code, reverse_code):
            self.code = code
            self.reverse_code = reverse_code

    class SeparateDatabaseAndState:
        def __init__(self, *, database_operations, state_operations):
            self.database_operations = database_operations
            self.state_operations = state_operations

    class AddField:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class CharField:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.name = None

        def set_attributes_from_name(self, name):
            self.name = name

    migrations = SimpleNamespace(
        Migration=type("Migration", (), {}),
        RunPython=RunPython,
        SeparateDatabaseAndState=SeparateDatabaseAndState,
        AddField=AddField,
    )
    django_db = types.ModuleType("django.db")
    django_db.migrations = migrations
    django_db.models = SimpleNamespace(CharField=CharField)
    monkeypatch.setitem(sys.modules, "django", types.ModuleType("django"))
    monkeypatch.setitem(sys.modules, "django.db", django_db)

    path = ROOT / "netbox_packer" / "migrations" / "0033_public_service_markers.py"
    spec = importlib.util.spec_from_file_location("migration_0033_behavior", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _TemplateQuery:
    def __init__(self, manager, filters):
        self.manager = manager
        self.filters = filters

    def update(self, **values):
        self.manager.updates.append((self.filters, values))
        for row in self.manager.rows:
            if all(row.get(name) == value for name, value in self.filters.items()):
                row.update(values)


class _TemplateManager:
    def __init__(self, rows=None):
        self.updates = []
        self.rows = rows or []

    def filter(self, **filters):
        return _TemplateQuery(self, filters)


def _migration_runtime(existing_columns, rows=None):
    manager = _TemplateManager(rows)
    model = SimpleNamespace(
        _meta=SimpleNamespace(db_table="netbox_packer_packertemplate"),
        objects=manager,
    )
    apps = SimpleNamespace(get_model=lambda *_args: model)
    introspection = SimpleNamespace(
        get_table_description=lambda *_args: [SimpleNamespace(name=name) for name in existing_columns]
    )
    connection = SimpleNamespace(cursor=lambda: _Cursor(), introspection=introspection)
    schema_editor = SimpleNamespace(connection=connection, add_field=lambda *args: added_fields.append(args))
    added_fields = []
    return apps, schema_editor, manager, added_fields


def test_migration_0033_adds_only_missing_public_state_and_reverses_without_data_loss(monkeypatch) -> None:
    migration = _load_migration_0033(monkeypatch)

    apps, schema_editor, manager, added_fields = _migration_runtime({"id"})
    migration.ensure_service_marker_column(apps, schema_editor)
    assert len(added_fields) == 1
    assert added_fields[0][1].name == "provisions_service"

    blank_name, blank_marker = migration.PUBLIC_SERVICE_MARKERS[0]
    custom_name, _custom_default = migration.PUBLIC_SERVICE_MARKERS[1]
    rows = [
        {"name": blank_name, "provisions_service": ""},
        {"name": custom_name, "provisions_service": "operator-custom"},
    ]
    apps, schema_editor, manager, added_fields = _migration_runtime({"id", "provisions_service", "legacy_data"}, rows)
    migration.ensure_service_marker_column(apps, schema_editor)
    assert added_fields == []
    migration.stamp_public_service_markers(apps, schema_editor)
    assert manager.updates == [
        (
            {"name": name, "provisions_service": ""},
            {"provisions_service": marker},
        )
        for name, marker in migration.PUBLIC_SERVICE_MARKERS
    ]
    assert rows[0]["provisions_service"] == blank_marker
    assert rows[1]["provisions_service"] == "operator-custom"

    database_operation = migration.Migration.operations[0].database_operations[0]
    data_operation = migration.Migration.operations[1]
    assert database_operation.reverse_code is migration.migrations.RunPython.noop
    assert data_operation.reverse_code is migration.migrations.RunPython.noop


def test_migration_graph_uses_public_non_destructive_boundary() -> None:
    migration_dir = ROOT / "netbox_packer" / "migrations"
    source = (migration_dir / "0033_public_service_markers.py").read_text()
    seed_dependency = (migration_dir / "0024_seed_akvorado_cloud_init.py").read_text()

    assert not (migration_dir / "0023_packertemplate_service_marker.py").exists()
    assert '("netbox_packer", "0022_update_fileserver_package_settings_comment")' in seed_dependency
    assert 'dependencies = [("netbox_packer", "0032_update_endpoint_authorization_descriptions")]' in source
    assert "SeparateDatabaseAndState" in source
    assert ".delete(" not in source
    assert "DROP COLUMN" not in source
    assert "splitlines" not in source
