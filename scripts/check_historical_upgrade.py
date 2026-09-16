#!/usr/bin/env python3
"""Seed and verify a non-destructive base-to-head database upgrade."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

CUSTOM_TEMPLATE = "influxdb-core-3.11.0-debian-13"
BLANK_TEMPLATE = "akvorado-2.4.0-ubuntu-2404"
CUSTOM_MARKER = "operator-custom-marker"
EXPECTED_BLANK_MARKER = "akvorado"
UPGRADE_MODELS = ("PackerTemplate", "PackerPluginSettings")


def _django_apps():
    import django
    from django.apps import apps

    django.setup()
    return apps


def _model_schema(apps) -> dict[str, dict[str, object]]:
    schema = {}
    for model_name in UPGRADE_MODELS:
        model = apps.get_model("netbox_packer", model_name)
        schema[model_name] = {
            "table": model._meta.db_table,
            "columns": sorted(field.column for field in model._meta.concrete_fields if field.column),
        }
    return schema


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return value


def _sentinel_value(field, index: int):
    field_type = field.get_internal_type()
    if field_type == "BooleanField":
        return True
    if field_type == "BinaryField":
        return f"legacy-binary-{index}".encode()
    if field_type == "JSONField":
        return {"legacy_sentinel": index}
    if field_type.endswith("IntegerField"):
        return index + 700
    if field_type in {"CharField", "TextField", "URLField"}:
        value = f"legacy-sentinel-{index}"
        return value[: field.max_length] if field.max_length else value
    raise RuntimeError(f"Unsupported legacy sentinel field type: {field_type}")


def _normalized(value):
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (dict, list)):
        return value
    return str(value)


def _legacy_fields(model, head_schema: dict) -> list:
    head_columns = set(head_schema[model.__name__]["columns"])
    return [field for field in model._meta.concrete_fields if field.column and field.column not in head_columns]


def _set_column(connection, model, row, field, value) -> dict[str, object]:
    quote = connection.ops.quote_name
    table = quote(model._meta.db_table)
    column = quote(field.column)
    pk_column = quote(model._meta.pk.column)
    prepared = field.get_db_prep_save(value, connection)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET {column} = %s WHERE {pk_column} = %s",
            [prepared, row.pk],
        )
        cursor.execute(
            f"SELECT {column} FROM {table} WHERE {pk_column} = %s",
            [row.pk],
        )
        stored = cursor.fetchone()[0]
    return {
        "model": model.__name__,
        "table": model._meta.db_table,
        "column": field.column,
        "pk_column": model._meta.pk.column,
        "pk": str(row.pk),
        "value": _normalized(stored),
    }


def capture_head(path: Path) -> None:
    apps = _django_apps()
    _write_json(path, {"models": _model_schema(apps)})


def _assert_historical_graph_applied() -> None:
    from django.db.migrations.recorder import MigrationRecorder

    applied = MigrationRecorder.Migration.objects.filter(app="netbox_packer", name__startswith="0023_").count()
    if applied != 1:
        raise RuntimeError(f"Expected exactly one applied historical 0023 migration, found {applied}")


def seed_base(head_path: Path, sentinel_path: Path) -> None:
    from django.db import connection

    apps = _django_apps()
    _assert_historical_graph_applied()
    head_schema = _read_json(head_path)["models"]
    template_model = apps.get_model("netbox_packer", "PackerTemplate")
    settings_model = apps.get_model("netbox_packer", "PackerPluginSettings")
    custom = template_model.objects.get(name=CUSTOM_TEMPLATE)
    blank = template_model.objects.get(name=BLANK_TEMPLATE)
    template_model.objects.filter(pk=custom.pk).update(provisions_service=CUSTOM_MARKER)
    template_model.objects.filter(pk=blank.pk).update(provisions_service="")
    settings_row, _created = settings_model.objects.get_or_create(singleton_key="default")

    sentinels = []
    for model, row in ((template_model, custom), (settings_model, settings_row)):
        for index, field in enumerate(_legacy_fields(model, head_schema), start=1):
            sentinels.append(_set_column(connection, model, row, field, _sentinel_value(field, index)))
    if not sentinels:
        raise RuntimeError("No base-only legacy columns were discovered; refusing a vacuous upgrade test")
    _write_json(sentinel_path, {"legacy": sentinels})


def _database_columns(connection, table: str) -> set[str]:
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table)
    return {column.name for column in description}


def _read_column(connection, sentinel: dict):
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {quote(sentinel['column'])} FROM {quote(sentinel['table'])} "
            f"WHERE {quote(sentinel['pk_column'])} = %s",
            [sentinel["pk"]],
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"Legacy sentinel row disappeared: {sentinel['model']}")
    return _normalized(row[0])


def assert_head(sentinel_path: Path) -> None:
    from django.db import connection

    apps = _django_apps()
    current_schema = _model_schema(apps)
    manifest = _read_json(sentinel_path)
    for sentinel in manifest["legacy"]:
        modeled_columns = set(current_schema[sentinel["model"]]["columns"])
        if sentinel["column"] in modeled_columns:
            raise RuntimeError(f"Legacy column remains exposed by the head model: {sentinel['column']}")
        if sentinel["column"] not in _database_columns(connection, sentinel["table"]):
            raise RuntimeError(f"Legacy database column was dropped: {sentinel['column']}")
        if _read_column(connection, sentinel) != sentinel["value"]:
            raise RuntimeError(f"Legacy sentinel data changed: {sentinel['column']}")

    template_model = apps.get_model("netbox_packer", "PackerTemplate")
    custom = template_model.objects.get(name=CUSTOM_TEMPLATE).provisions_service
    blank = template_model.objects.get(name=BLANK_TEMPLATE).provisions_service
    if custom != CUSTOM_MARKER:
        raise RuntimeError(f"Custom public marker changed during upgrade: {custom!r}")
    if blank != EXPECTED_BLANK_MARKER:
        raise RuntimeError(f"Blank public marker was not stamped during upgrade: {blank!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("capture-head", "seed-base", "assert-head"))
    parser.add_argument("--head-schema", type=Path)
    parser.add_argument("--sentinels", type=Path)
    args = parser.parse_args()
    if args.phase == "capture-head" and args.head_schema:
        capture_head(args.head_schema)
        return
    if args.phase == "seed-base" and args.head_schema and args.sentinels:
        seed_base(args.head_schema, args.sentinels)
        return
    if args.phase == "assert-head" and args.sentinels:
        assert_head(args.sentinels)
        return
    parser.error("The selected phase is missing its required manifest argument")


if __name__ == "__main__":
    main()
