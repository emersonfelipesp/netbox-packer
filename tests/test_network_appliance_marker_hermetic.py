"""Hermetic behavior tests for the generic network appliance marker migration.

The migration only needs ``django.db.migrations`` at import time; when Django is
absent a minimal stub is registered so the seed logic can still be exercised.
The ORM is replaced by an in-memory fake model so no database is required.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "netbox_packer/migrations/0033_seed_network_appliance_marker.py"


def _ensure_django_migrations_stub() -> None:
    if importlib.util.find_spec("django") is not None:
        return
    migrations = types.ModuleType("django.db.migrations")
    migrations.RunPython = lambda forward, reverse=None: (forward, reverse)  # type: ignore[attr-defined]
    migrations.Migration = type("Migration", (), {})  # type: ignore[attr-defined]
    db = types.ModuleType("django.db")
    db.migrations = migrations  # type: ignore[attr-defined]
    django = types.ModuleType("django")
    django.db = db  # type: ignore[attr-defined]
    sys.modules.setdefault("django", django)
    sys.modules.setdefault("django.db", db)
    sys.modules.setdefault("django.db.migrations", migrations)


def _load_migration() -> Any:
    _ensure_django_migrations_stub()
    spec = importlib.util.spec_from_file_location("netbox_packer_migration_0033_under_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


class _DoesNotExist(Exception):
    pass


class _Row:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self.saved_fields: list[str] | None = None

    def save(self, update_fields: list[str] | None = None) -> None:
        self.saved_fields = list(update_fields or [])


class _QuerySet:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows

    def update(self, **values: Any) -> int:
        for row in self.rows:
            row.__dict__.update(values)
            row.saved_fields = sorted(values)
        return len(self.rows)


class _Manager:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows
        self.created: list[dict[str, Any]] = []
        self.before_update: Any = None

    def filter(self, *, pk: Any, proxmox_template_id__in: tuple[int, ...], **exact: Any) -> _QuerySet:
        if self.before_update is not None:
            self.before_update()
        matched = [
            row
            for row in self.rows
            if row.pk == pk
            and row.proxmox_template_id in proxmox_template_id__in
            and all(getattr(row, field) == value for field, value in exact.items())
        ]
        return _QuerySet(matched)

    def get(self, *, name: str) -> _Row:
        for row in self.rows:
            if row.name == name:
                return row
        raise _DoesNotExist(name)

    def create(self, **fields: Any) -> _Row:
        self.created.append(fields)
        row = _Row(**fields)
        self.rows.append(row)
        return row


class _Model:
    DoesNotExist = _DoesNotExist

    def __init__(self, rows: list[_Row]) -> None:
        self.objects = _Manager(rows)


class _Apps:
    def __init__(self, model: _Model) -> None:
        self.model = model

    def get_model(self, app_label: str, model_name: str) -> _Model:
        assert (app_label, model_name) == ("netbox_packer", "PackerTemplate")
        return self.model


def test_clean_apply_creates_marked_placeholder_row() -> None:
    model = _Model([])
    migration.seed_network_appliance_marker(_Apps(model), None)
    (created,) = model.objects.created
    assert created["name"] == "network-appliance-debian-13"
    assert created["provisions_service"] == "network-appliance"
    assert created["installer_config"] is None
    assert created["build_status"] == "pending"
    assert created["proxmox_template_id"] == 0


def _profile_row(**overrides: Any) -> _Row:
    fields: dict[str, Any] = {
        "pk": 1,
        "name": migration.TEMPLATE_NAME,
        "provisions_service": "",
        "os_family": "debian",
        "os_version": "13",
        "proxmox_template_id": 9700,
        "proxmox_node": "vpve",
    }
    fields.update(overrides)
    return _Row(**fields)


def test_existing_row_with_empty_marker_is_stamped_only() -> None:
    row = _profile_row()
    model = _Model([row])
    migration.seed_network_appliance_marker(_Apps(model), None)
    assert row.provisions_service == migration.SERVICE_MARKER
    assert row.saved_fields == ["provisions_service"]
    assert row.proxmox_node == "vpve"
    assert model.objects.created == []


def test_existing_row_with_other_marker_aborts_untouched() -> None:
    row = _profile_row(provisions_service="akvorado")
    model = _Model([row])
    with pytest.raises(RuntimeError, match="provisions_service"):
        migration.seed_network_appliance_marker(_Apps(model), None)
    assert row.provisions_service == "akvorado"
    assert row.saved_fields is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"os_family": "ubuntu"}, "os_family"),
        ({"os_version": "12"}, "os_version"),
        ({"proxmox_template_id": 9053}, "proxmox_template_id"),
    ],
)
def test_existing_row_with_different_identity_aborts_untouched(overrides: dict[str, Any], field: str) -> None:
    row = _profile_row(**overrides)
    model = _Model([row])
    with pytest.raises(RuntimeError, match=field):
        migration.seed_network_appliance_marker(_Apps(model), None)
    assert row.provisions_service == ""
    assert row.saved_fields is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"os_family": "ubuntu"}, "os_family"),
        ({"os_version": "12"}, "os_version"),
        ({"proxmox_template_id": 9053}, "proxmox_template_id"),
    ],
)
def test_marked_row_with_drifted_identity_aborts(overrides: dict[str, Any], field: str) -> None:
    row = _profile_row(provisions_service=migration.SERVICE_MARKER, **overrides)
    model = _Model([row])
    with pytest.raises(RuntimeError, match=field):
        migration.seed_network_appliance_marker(_Apps(model), None)
    assert row.saved_fields is None


def test_identity_changed_between_read_and_write_aborts() -> None:
    row = _profile_row()
    model = _Model([row])

    def drift() -> None:
        row.os_family = "ubuntu"

    model.objects.before_update = drift
    with pytest.raises(RuntimeError, match="changed while the migration ran"):
        migration.seed_network_appliance_marker(_Apps(model), None)
    assert row.provisions_service == ""


def test_existing_placeholder_vmid_row_is_stamped() -> None:
    row = _profile_row(proxmox_template_id=0)
    model = _Model([row])
    migration.seed_network_appliance_marker(_Apps(model), None)
    assert row.provisions_service == migration.SERVICE_MARKER


def test_forward_is_idempotent_and_ignores_unrelated_rows() -> None:
    other = _Row(name="unrelated-template", provisions_service="")
    model = _Model([other])
    migration.seed_network_appliance_marker(_Apps(model), None)
    migration.seed_network_appliance_marker(_Apps(model), None)
    assert len(model.objects.created) == 1
    assert other.provisions_service == ""
    assert other.saved_fields is None


def test_reverse_is_a_noop() -> None:
    migration.unseed_network_appliance_marker(_Apps(_Model([])), None)


def test_migration_source_declares_dependency_and_runpython() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert '("netbox_packer", "0032_update_endpoint_authorization_descriptions")' in source
    assert "migrations.RunPython(" in source
    assert "seed_network_appliance_marker" in source
