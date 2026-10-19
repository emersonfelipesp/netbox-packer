"""Hermetic tests for the retained-column compatibility step of the seed migrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "netbox_packer/migrations/_legacy_agent_columns.py"
TABLE = "netbox_packer_packertemplate"


class _Cursor:
    def __init__(self, relation, rows: list[tuple[str, str]], queries: list[tuple[str, list]]) -> None:
        self._relation = relation
        self._rows = rows
        self._queries = queries

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: list) -> None:
        self._queries.append((sql, params))

    def fetchone(self):
        return self._relation

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows


class _Apps:
    def __init__(self, mapped: tuple[str, ...]) -> None:
        self._mapped = mapped

    def get_model(self, app_label: str, model_name: str):
        assert (app_label, model_name) == ("netbox_packer", "PackerTemplate")
        fields = [SimpleNamespace(column=name) for name in self._mapped]
        return SimpleNamespace(_meta=SimpleNamespace(db_table=TABLE, local_concrete_fields=fields))


class _SchemaEditor:
    def __init__(self, vendor: str, rows: list[tuple[str, str]], schema: str | None = "public") -> None:
        self.executed: list[str] = []
        self.queries: list[tuple[str, list]] = []
        relation = None if schema is None else (schema, TABLE, 4242)
        self.connection = SimpleNamespace(vendor=vendor, cursor=lambda: _Cursor(relation, rows, self.queries))

    def quote_name(self, name: str) -> str:
        return f'"{name}"'

    def execute(self, statement: str) -> None:
        self.executed.append(statement)


def _load_helper():
    spec = importlib.util.spec_from_file_location("legacy_agent_columns_under_test", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statement(column: str, default: str, schema: str = "public") -> str:
    return f'ALTER TABLE "{schema}"."{TABLE}" ALTER COLUMN "{column}" SET DEFAULT {default}'


def test_unmapped_required_columns_get_neutral_defaults_in_order() -> None:
    helper = _load_helper()
    editor = _SchemaEditor(
        "postgresql",
        [("legacy_flag", "bool"), ("legacy_url", "varchar"), ("legacy_count", "int4")],
    )

    helper.ensure_legacy_column_defaults(_Apps(("id", "name")), editor)

    assert editor.executed == [
        _statement("legacy_flag", "false"),
        _statement("legacy_url", "''"),
        _statement("legacy_count", "0"),
    ]
    resolve_sql, resolve_params = editor.queries[0]
    assert resolve_params == [f'"{TABLE}"']
    assert "to_regclass(%s)" in resolve_sql
    columns_sql, columns_params = editor.queries[1]
    assert columns_params == [4242]
    assert "a.attnotnull AND NOT a.atthasdef" in columns_sql
    assert "a.attidentity = ''" in columns_sql
    assert "current_schema" not in columns_sql + resolve_sql


def test_columns_known_to_the_model_state_are_never_touched() -> None:
    helper = _load_helper()
    editor = _SchemaEditor("postgresql", [("name", "varchar"), ("legacy_flag", "bool")])

    helper.ensure_legacy_column_defaults(_Apps(("id", "name")), editor)

    assert editor.executed == [_statement("legacy_flag", "false")]


def test_a_column_type_without_a_neutral_default_is_left_alone() -> None:
    helper = _load_helper()
    editor = _SchemaEditor("postgresql", [("legacy_blob", "jsonb"), ("legacy_when", "timestamptz")])

    helper.ensure_legacy_column_defaults(_Apps(("id",)), editor)

    assert editor.executed == []


def test_no_required_unmapped_column_is_a_noop() -> None:
    helper = _load_helper()
    editor = _SchemaEditor("postgresql", [])
    helper.ensure_legacy_column_defaults(_Apps(("id", "name")), editor)
    assert editor.executed == []


def test_non_postgresql_vendor_is_a_noop_and_never_queries() -> None:
    helper = _load_helper()
    editor = _SchemaEditor("sqlite", [("legacy_flag", "bool")])
    helper.ensure_legacy_column_defaults(_Apps(("id",)), editor)
    assert editor.executed == []
    assert editor.queries == []


def test_the_step_is_idempotent() -> None:
    helper = _load_helper()
    editor = _SchemaEditor("postgresql", [("legacy_flag", "bool")])
    helper.ensure_legacy_column_defaults(_Apps(("id",)), editor)
    helper.ensure_legacy_column_defaults(_Apps(("id",)), editor)
    assert editor.executed == [_statement("legacy_flag", "false")] * 2


def test_helper_is_not_a_migration_and_hard_codes_no_column_name() -> None:
    assert HELPER.name.startswith("_")
    source = HELPER.read_text(encoding="utf-8")
    assert "ALTER COLUMN" in source
    # Column names come from the database catalog at run time, never from a literal.
    assert 'ALTER COLUMN "' not in source
    assert "ALTER COLUMN '" not in source


def test_the_table_found_through_the_search_path_is_the_one_altered() -> None:
    """A table in another schema of the search path is inspected and altered by its real schema."""
    helper = _load_helper()
    editor = _SchemaEditor("postgresql", [("legacy_flag", "bool")], schema="public")

    helper.ensure_legacy_column_defaults(_Apps(("id",)), editor)

    assert editor.executed == [_statement("legacy_flag", "false", schema="public")]
    assert "public" in editor.executed[0] and '"netbox"' not in editor.executed[0]


def test_a_table_the_database_cannot_resolve_is_a_noop() -> None:
    helper = _load_helper()
    editor = _SchemaEditor("postgresql", [("legacy_flag", "bool")], schema=None)
    helper.ensure_legacy_column_defaults(_Apps(("id",)), editor)
    assert editor.executed == []
    assert len(editor.queries) == 1
