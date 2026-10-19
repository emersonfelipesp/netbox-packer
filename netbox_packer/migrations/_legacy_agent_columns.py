"""Keep seeds compatible with physical columns retained by old installations.

Migration ``0033_public_service_markers`` establishes the public model state with
``SeparateDatabaseAndState``. A database that applied an earlier migration of this
plugin can keep columns that no migration dropped and that the current model state
does not know. Such a column can be ``NOT NULL`` without a database default, and a
historical model that inserts a seed row without the field then fails with a
not-null violation.

Before a seed inserts a row, give every unmapped ``NOT NULL`` column that has no
default a neutral database default. The table is resolved through ``to_regclass`` so
that the relation found through ``search_path`` is the one inspected and altered. No
row is changed, no column is dropped, and the model state is untouched. Columns whose
type has no neutral default are left alone.
"""

from __future__ import annotations

# pg_type.typname -> neutral SQL default
NEUTRAL_DEFAULTS_BY_TYPE = {
    "bool": "false",
    "varchar": "''",
    "bpchar": "''",
    "text": "''",
    "int2": "0",
    "int4": "0",
    "int8": "0",
}

# Resolve the table exactly as the database resolves an unqualified name (search_path).
_RESOLVE_RELATION = (
    "SELECT n.nspname, c.relname, c.oid FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = to_regclass(%s)"
)

_UNMAPPED_REQUIRED_COLUMNS = (
    "SELECT a.attname, t.typname FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid "
    "WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull "
    "AND NOT a.atthasdef AND a.attidentity = '' AND a.attgenerated = '' "
    "ORDER BY a.attnum"
)


def ensure_legacy_column_defaults(apps, schema_editor) -> None:
    """Set neutral defaults for retained required columns on PostgreSQL."""

    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    template_model = apps.get_model("netbox_packer", "PackerTemplate")
    table_name = template_model._meta.db_table
    mapped = {field.column for field in template_model._meta.local_concrete_fields}
    with connection.cursor() as cursor:
        cursor.execute(_RESOLVE_RELATION, [schema_editor.quote_name(table_name)])
        relation = cursor.fetchone()
        if relation is None:
            return
        schema_name, relation_name, relation_oid = relation
        cursor.execute(_UNMAPPED_REQUIRED_COLUMNS, [relation_oid])
        required = cursor.fetchall()

    qualified = f"{schema_editor.quote_name(schema_name)}.{schema_editor.quote_name(relation_name)}"
    for column_name, type_name in required:
        default_sql = NEUTRAL_DEFAULTS_BY_TYPE.get(type_name)
        if column_name in mapped or default_sql is None:
            continue
        schema_editor.execute(
            f"ALTER TABLE {qualified} ALTER COLUMN {schema_editor.quote_name(column_name)} SET DEFAULT {default_sql}"
        )
