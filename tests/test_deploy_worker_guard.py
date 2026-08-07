"""Executable contract tests for guard-aware NetBox plugin deployment."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow_script() -> str:
    workflow = yaml.safe_load((ROOT / ".gitea" / "workflows" / "deploy-production.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["deploy"]["steps"][0]["run"]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _unit_key(unit: str) -> str:
    return unit.replace(".", "_").replace("-", "_")


def _active_path(state_dir: Path, unit: str) -> Path:
    return state_dir / f"active.{_unit_key(unit)}"


def _prepare_guard(
    tmp_path: Path,
    *,
    target: str = "staging",
    helper_mode: str = "success",
    initially_active: bool = True,
) -> tuple[str, dict[str, str], Path, str, str]:
    state_dir = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    state_dir.mkdir(exist_ok=True)
    bin_dir.mkdir(exist_ok=True)
    if target == "staging":
        web_unit = "netbox-staging.service"
        worker_unit = "netbox-staging-rq.service"
        helper_assignment = "deploy_helper=/opt/nmulticloud/deploy/bin/deploy-netbox-plugin-staging"
    else:
        web_unit = "netbox.service"
        worker_unit = "netbox-rq.service"
        helper_assignment = "deploy_helper=/opt/nmulticloud/deploy/bin/deploy-netbox-plugin"

    for index, unit in enumerate((web_unit, worker_unit), start=1):
        _active_path(state_dir, unit).write_text("active\n" if initially_active else "inactive\n", encoding="utf-8")
        (state_dir / f"pid.{_unit_key(unit)}").write_text(
            f"11{index}\n" if initially_active else "0\n", encoding="utf-8"
        )

    _write_executable(
        bin_dir / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
command_name="${1:-}"
shift || true
unit="${*: -1}"
unit_key="${unit//[^A-Za-z0-9]/_}"
case "$command_name" in
  is-active)
    [[ "$(cat "$FAKE_SYSTEMD_DIR/active.$unit_key")" == "active" ]]
    ;;
  show)
    cat "$FAKE_SYSTEMD_DIR/pid.$unit_key"
    ;;
  *) exit 2 ;;
esac
""",
    )
    helper = tmp_path / "deploy-helper"
    _write_executable(
        helper,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--supports" ]]; then
  [[ "$FAKE_HELPER_MODE" != "legacy" && "${2:-}" == "netbox-service-guard-v1" ]]
  exit
fi
[[ "${NMC_NETBOX_DEPLOY_REQUIRE_SERVICE_GUARD:-}" == "netbox-service-guard-v1" ]]
[[ -n "${NMC_NETBOX_DEPLOY_WEB_UNIT:-}" && -n "${NMC_NETBOX_DEPLOY_WORKER_UNIT:-}" ]]
exec 8>"$FAKE_SYSTEMD_DIR/global.lock"
flock 8
printf 'mutation-start %s %s %s\n' \
  "$$" "$NMC_NETBOX_DEPLOY_WEB_UNIT" "$NMC_NETBOX_DEPLOY_WORKER_UNIT" \
  >>"$FAKE_SYSTEMD_DIR/log"
for unit in "$NMC_NETBOX_DEPLOY_WEB_UNIT" "$NMC_NETBOX_DEPLOY_WORKER_UNIT"; do
  key="${unit//[^A-Za-z0-9]/_}"
  printf 'inactive\n' >"$FAKE_SYSTEMD_DIR/active.$key"
  printf '0\n' >"$FAKE_SYSTEMD_DIR/pid.$key"
done
case "$FAKE_HELPER_MODE" in
  failure)
    printf 'mutation-failed %s\n' "$$" >>"$FAKE_SYSTEMD_DIR/log"
    exit 42
    ;;
  success|no_runtime_change)
    sleep 0.2
    for unit in "$NMC_NETBOX_DEPLOY_WEB_UNIT" "$NMC_NETBOX_DEPLOY_WORKER_UNIT"; do
      key="${unit//[^A-Za-z0-9]/_}"
      printf 'active\n' >"$FAKE_SYSTEMD_DIR/active.$key"
      printf '%s\n' "$((2000 + $$))" >"$FAKE_SYSTEMD_DIR/pid.$key"
    done
    ;;
  *) exit 2 ;;
esac
printf 'mutation-end %s\n' "$$" >>"$FAKE_SYSTEMD_DIR/log"
""",
    )

    script = _workflow_script().replace(
        f"{helper_assignment}\n",
        f"deploy_helper={shlex.quote(str(helper))}\n",
        1,
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_SYSTEMD_DIR": str(state_dir),
        "FAKE_HELPER_MODE": helper_mode,
        "REF": "main",
        "REQUESTED_ENVIRONMENT": target,
        "GITHUB_REF_NAME": "main",
    }
    return script, env, state_dir, web_unit, worker_unit


def _run_guard(
    tmp_path: Path,
    **kwargs,
) -> tuple[subprocess.CompletedProcess[str], Path, str, str]:
    script, env, state_dir, web_unit, worker_unit = _prepare_guard(tmp_path, **kwargs)
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, state_dir, web_unit, worker_unit


def _assert_active(state_dir: Path, *units: str) -> None:
    for unit in units:
        assert _active_path(state_dir, unit).read_text(encoding="utf-8").strip() == "active"
        assert int((state_dir / f"pid.{_unit_key(unit)}").read_text(encoding="utf-8")) > 0


@pytest.mark.parametrize("target", ("staging", "production"))
def test_guard_contract_forwards_exact_environment_units(tmp_path: Path, target: str) -> None:
    result, state_dir, web_unit, worker_unit = _run_guard(tmp_path, target=target)

    assert result.returncode == 0, result.stderr
    _assert_active(state_dir, web_unit, worker_unit)
    log = (state_dir / "log").read_text(encoding="utf-8")
    assert "mutation-start " in log
    assert f" {web_unit} {worker_unit}" in log


def test_legacy_helper_is_rejected_before_service_or_runtime_mutation(tmp_path: Path) -> None:
    result, state_dir, web_unit, worker_unit = _run_guard(
        tmp_path,
        helper_mode="legacy",
    )

    assert result.returncode != 0
    assert "deploy the coordinated helper update first" in result.stdout
    _assert_active(state_dir, web_unit, worker_unit)
    assert not (state_dir / "log").exists()


def test_guard_aware_helper_failure_leaves_both_services_stopped(tmp_path: Path) -> None:
    result, state_dir, web_unit, worker_unit = _run_guard(
        tmp_path,
        helper_mode="failure",
    )

    assert result.returncode == 42
    for unit in (web_unit, worker_unit):
        assert _active_path(state_dir, unit).read_text(encoding="utf-8").strip() == "inactive"


@pytest.mark.parametrize("initially_active", (True, False))
def test_no_runtime_change_contract_starts_fresh_services(
    tmp_path: Path,
    initially_active: bool,
) -> None:
    result, state_dir, web_unit, worker_unit = _run_guard(
        tmp_path,
        helper_mode="no_runtime_change",
        initially_active=initially_active,
    )

    assert result.returncode == 0, result.stderr
    _assert_active(state_dir, web_unit, worker_unit)


def test_concurrent_deployments_serialize_mutation_and_service_lifecycle(tmp_path: Path) -> None:
    script, env, state_dir, _web_unit, _worker_unit = _prepare_guard(tmp_path)
    first = subprocess.Popen(
        ["bash", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    second = subprocess.Popen(
        ["bash", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    first_stdout, first_stderr = first.communicate(timeout=10)
    second_stdout, second_stderr = second.communicate(timeout=10)

    assert first.returncode == 0, first_stdout + first_stderr
    assert second.returncode == 0, second_stdout + second_stderr
    events = (state_dir / "log").read_text(encoding="utf-8").splitlines()
    assert len(events) == 4
    assert events[0].startswith("mutation-start ")
    assert events[1].startswith("mutation-end ")
    assert events[2].startswith("mutation-start ")
    assert events[3].startswith("mutation-end ")
    assert events[0].split()[1] == events[1].split()[1]
    assert events[2].split()[1] == events[3].split()[1]
    assert events[0].split()[1] != events[2].split()[1]
