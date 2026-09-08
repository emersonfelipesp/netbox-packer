#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NETBOX_REF="${NETBOX_REF:-00791344e68213bde942218283dce03cc3941c30}"
WORK_BASE="${RUNNER_TEMP:-/tmp}/netbox-source-${NETBOX_REF#v}"
NETBOX_DIR="${NETBOX_SOURCE_DIR:-$WORK_BASE/netbox}"
VENV_DIR="${NETBOX_VENV_DIR:-$WORK_BASE/venv}"
CONFIG_DIR="$WORK_BASE/config"
PYTHON_BIN="${PYTHON_BIN:-python}"

: "${NETBOX_PLUGINS_JSON:?Set NETBOX_PLUGINS_JSON, for example '[\"netbox_packer\"]'}"
: "${MIGRATION_APPS:?Set MIGRATION_APPS, for example 'netbox_packer'}"

if [ -z "${NETBOX_SOURCE_DIR:-}" ]; then
  if [ ! -d "$NETBOX_DIR/.git" ]; then
    rm -rf "$NETBOX_DIR"
    git clone --no-checkout https://github.com/netbox-community/netbox.git "$NETBOX_DIR"
  fi
  git -C "$NETBOX_DIR" fetch --depth=1 origin "$NETBOX_REF"
  git -C "$NETBOX_DIR" checkout --detach "$NETBOX_REF"
  test "$(git -C "$NETBOX_DIR" rev-parse HEAD)" = "$NETBOX_REF"
else
  echo "Using NetBox source from NETBOX_SOURCE_DIR=$NETBOX_SOURCE_DIR"
fi

rm -rf "$VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"
PY="$VENV_DIR/bin/python"

"$PY" -m pip install "pip==25.2" "wheel==0.46.1" "setuptools==80.9.0" "hatchling==1.27.0"
"$PY" -m pip install -r "$NETBOX_DIR/requirements.txt"
"$PY" -m pip install --no-build-isolation --no-deps -e "$ROOT_DIR"

mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/configuration.py" <<'PY'
import json
import os

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split()

DATABASES = {
    "default": {
        "NAME": os.environ.get("DB_NAME", "netbox"),
        "USER": os.environ.get("DB_USER", "netbox"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "netbox"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 300,
    }
}

REDIS = {
    "tasks": {
        "HOST": os.environ.get("REDIS_HOST", "localhost"),
        "PORT": int(os.environ.get("REDIS_PORT", "6379")),
        "USERNAME": "",
        "PASSWORD": os.environ.get("REDIS_PASSWORD", ""),
        "DATABASE": int(os.environ.get("REDIS_DATABASE", "0")),
        "SSL": False,
    },
    "caching": {
        "HOST": os.environ.get("REDIS_HOST", "localhost"),
        "PORT": int(os.environ.get("REDIS_PORT", "6379")),
        "USERNAME": "",
        "PASSWORD": os.environ.get("REDIS_PASSWORD", ""),
        "DATABASE": int(os.environ.get("REDIS_CACHE_DATABASE", "1")),
        "SSL": False,
    },
}

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "netbox-source-ci-secret-key-not-for-production-000000000000",
)
API_TOKEN_PEPPERS = {
    1: "netbox-source-ci-pepper-not-for-production-000000000000000000",
}
DEFAULT_PERMISSIONS = {}
RQ = {"COMMIT_MODE": "auto"}

PLUGINS = json.loads(os.environ["NETBOX_PLUGINS_JSON"])
PLUGINS_CONFIG = {
    "netbox_packer": {},
}
PY

export NETBOX_CONFIGURATION=configuration
export PYTHONPATH="$CONFIG_DIR:$NETBOX_DIR/netbox:$ROOT_DIR:${PYTHONPATH:-}"

"$PY" "$NETBOX_DIR/netbox/manage.py" check
"$PY" "$NETBOX_DIR/netbox/manage.py" migrate --no-input
"$PY" "$NETBOX_DIR/netbox/manage.py" makemigrations $MIGRATION_APPS --check --dry-run

if [ -n "${TEST_LABELS:-}" ]; then
  TEST_OUTPUT="$WORK_BASE/test-output.txt"
  if ! "$PY" "$NETBOX_DIR/netbox/manage.py" test $TEST_LABELS -v 2 >"$TEST_OUTPUT" 2>&1; then
    cat "$TEST_OUTPUT"
    exit 1
  fi
  cat "$TEST_OUTPUT"
  grep -Eq 'Ran [1-9][0-9]* test' "$TEST_OUTPUT"
fi
