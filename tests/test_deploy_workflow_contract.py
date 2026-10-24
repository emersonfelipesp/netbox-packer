"""Contract tests for the staging/production deployment workflow.

The workflow is parsed as YAML and its single deploy step is executed with
bash against stub helpers in a temporary directory, so no case can reach a
deploy host. The expectations are a fixed matrix, not derived from the
workflow under test.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".gitea/workflows/deploy-production.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
STAGING_HELPER = "deploy-netbox-plugin-staging"
PRODUCTION_HELPER = "deploy-netbox-plugin"

# Input names sent by the registered staging and production deployment
# targets. Renaming them breaks dispatches from the managed deployment surface.
REGISTERED_TARGET_INPUTS = {"ref", "environment"}


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict:
    document = yaml.safe_load(_workflow_text())
    assert isinstance(document, dict), "deploy workflow must be a YAML mapping"
    return document


def _triggers(document: dict) -> dict:
    # PyYAML 1.1 reads the bare key `on` as boolean True.
    triggers = document.get("on", document.get(True))
    assert isinstance(triggers, dict), "deploy workflow must declare its triggers"
    return triggers


def _deploy_steps(document: dict) -> list[dict]:
    jobs = document.get("jobs")
    assert isinstance(jobs, dict) and list(jobs) == ["deploy"], "expected exactly one deploy job"
    steps = jobs["deploy"].get("steps")
    assert isinstance(steps, list) and steps, "deploy job must have steps"
    return steps


def _deploy_script() -> str:
    steps = _deploy_steps(_workflow())
    scripts = [step["run"] for step in steps if isinstance(step, dict) and "run" in step]
    assert len(scripts) == 1, "expected exactly one shell step"
    return scripts[0]


def test_main_push_never_deploys() -> None:
    triggers = _triggers(_workflow())
    assert triggers["push"] == {"branches": ["develop"]}
    assert set(triggers) == {"push", "workflow_dispatch"}


def test_dispatch_inputs_cover_registered_targets() -> None:
    inputs = _triggers(_workflow())["workflow_dispatch"]["inputs"]
    assert set(inputs) == REGISTERED_TARGET_INPUTS
    assert inputs["environment"]["options"] == ["staging", "production"]


def test_workflow_has_no_remote_shell_path() -> None:
    text = _workflow_text()
    for token in (r"\bssh\b", r"\bscp\b", r"StrictHostKeyChecking", r"\brsync\b", r"deploy-plugin\b"):
        assert re.search(token, text) is None, f"forbidden remote-shell token {token!r} in workflow"


def test_only_managed_helpers_are_invoked() -> None:
    script = _deploy_script()
    helpers = set(re.findall(r"\$\{DEPLOY_BIN\}/([A-Za-z0-9_.-]+)", script))
    assert helpers == {STAGING_HELPER, PRODUCTION_HELPER}
    job = _workflow()["jobs"]["deploy"]
    assert job["runs-on"] == "prod-deploy"
    assert job["env"]["DEPLOY_BIN"] == "/opt/nmulticloud/deploy/bin"
    assert "${{" not in script, "the step script must read inputs from env, not interpolate expressions"


def test_omitted_inputs_resolve_to_empty_values() -> None:
    # Gitea applies declared defaults to omitted dispatch inputs. Neither input
    # may default to a value that overrides the branch-based target decision.
    inputs = _triggers(_workflow())["workflow_dispatch"]["inputs"]
    assert "default" not in inputs["environment"]
    assert inputs["ref"].get("default", "") == ""


# Exact job and step shape, transcribed once from the reviewed workflow. Any
# extra step, condition, binding, or failure override must be reviewed here.
EXPECTED_JOB_KEYS = {"name", "if", "runs-on", "timeout-minutes", "env", "steps"}
EXPECTED_JOB_ENV = {
    "DEPLOY_BIN": "/opt/nmulticloud/deploy/bin",
    "EVENT_NAME": "${{ github.event_name }}",
    "REQUESTED_REF": "${{ inputs.ref }}",
    "REQUESTED_ENVIRONMENT": "${{ inputs.environment }}",
}


def test_job_and_step_shape_is_exact() -> None:
    document = _workflow()
    top_level = {"on" if key is True else key for key in document}
    assert top_level == {"name", "on", "permissions", "concurrency", "jobs"}
    assert document["permissions"] == {"contents": "read"}
    assert document["concurrency"]["cancel-in-progress"] is False
    job = document["jobs"]["deploy"]
    assert set(job) == EXPECTED_JOB_KEYS
    assert job["if"] == "${{ github.repository == 'emersonfelipesp/netbox-packer' }}"
    assert job["env"] == EXPECTED_JOB_ENV
    steps = _deploy_steps(document)
    assert len(steps) == 1
    assert set(steps[0]) == {"name", "shell", "run"}
    assert steps[0]["shell"] == "bash"
    script = steps[0]["run"]
    assert script.lstrip().startswith("set -euo pipefail")
    assert "|| true" not in script
    assert re.search(r'^\s*"\$helper" netbox-packer "\$commit"\s*$', script, re.MULTILINE)


@pytest.fixture()
def helper_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in (STAGING_HELPER, PRODUCTION_HELPER):
        helper = bin_dir / name
        helper.write_text(f'#!/usr/bin/env bash\nprintf "%s %s\\n" "{name}" "$*" >> "$CALL_LOG"\n', encoding="utf-8")
        helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(helper_dir: Path, *, event: str, ref: str, requested_ref: str = "", environment: str = "", sha: str = SHA):
    bash = shutil.which("bash")
    if bash is None:
        pytest.fail("bash is required to execute the deploy step contract")
    call_log = helper_dir.parent / "calls.log"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CALL_LOG": str(call_log),
        "DEPLOY_BIN": str(helper_dir),
        "EVENT_NAME": event,
        "GITHUB_REF": ref,
        "GITHUB_SHA": sha,
        "REQUESTED_REF": requested_ref,
        "REQUESTED_ENVIRONMENT": environment,
    }
    result = subprocess.run(
        [bash, "-c", _deploy_script()], env=env, capture_output=True, text=True, timeout=30, check=False
    )
    calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    return result, calls


ALLOWED = [
    # event, ref, requested_ref, environment, expected helper
    ("push", "refs/heads/develop", "", "", STAGING_HELPER),
    ("workflow_dispatch", "refs/heads/develop", "develop", "staging", STAGING_HELPER),
    ("workflow_dispatch", "refs/heads/develop", "", "", STAGING_HELPER),
    ("workflow_dispatch", "refs/heads/develop", SHA, "staging", STAGING_HELPER),
    ("workflow_dispatch", "refs/heads/main", "main", "production", PRODUCTION_HELPER),
    ("workflow_dispatch", "refs/heads/main", "", "production", PRODUCTION_HELPER),
    ("workflow_dispatch", "refs/heads/main", "", "", PRODUCTION_HELPER),
    ("workflow_dispatch", "refs/heads/main", SHA, "production", PRODUCTION_HELPER),
]


@pytest.mark.parametrize(("event", "ref", "requested_ref", "environment", "helper"), ALLOWED)
def test_allowed_combinations_deploy_the_exact_run_commit(
    helper_dir: Path, event: str, ref: str, requested_ref: str, environment: str, helper: str
) -> None:
    result, calls = _run(helper_dir, event=event, ref=ref, requested_ref=requested_ref, environment=environment)
    assert result.returncode == 0, result.stderr
    assert calls == [f"{helper} netbox-packer {SHA}"]


REFUSED = [
    # A push to main is the retired automatic production deploy.
    ("push", "refs/heads/main", "", ""),
    ("push", "refs/heads/feature", "", ""),
    ("push", "refs/tags/v0.0.5", "", ""),
    # The environment may only confirm the branch.
    ("workflow_dispatch", "refs/heads/develop", "", "production"),
    ("workflow_dispatch", "refs/heads/main", "", "staging"),
    ("workflow_dispatch", "refs/heads/feature", "", "production"),
    ("workflow_dispatch", "refs/heads/feature", "", "staging"),
    ("workflow_dispatch", "refs/tags/v0.0.5", "", "production"),
    ("workflow_dispatch", "refs/heads/main", "", "Production"),
    ("pull_request", "refs/heads/main", "", "production"),
    # The ref input can never select another commit.
    ("workflow_dispatch", "refs/heads/main", "develop", "production"),
    ("workflow_dispatch", "refs/heads/main", "v0.0.5", "production"),
    ("workflow_dispatch", "refs/heads/main", OTHER_SHA, "production"),
    ("workflow_dispatch", "refs/heads/main", SHA[:12], "production"),
    ("workflow_dispatch", "refs/heads/main", "main;id", "production"),
    ("workflow_dispatch", "refs/heads/main", "main$(id)", "production"),
    ("workflow_dispatch", "refs/heads/develop", "main", "staging"),
]


@pytest.mark.parametrize(
    ("ref", "environment", "helper"),
    [("refs/heads/main", "production", PRODUCTION_HELPER), ("refs/heads/develop", "staging", STAGING_HELPER)],
)
def test_a_different_run_commit_is_passed_through(helper_dir: Path, ref: str, environment: str, helper: str) -> None:
    result, calls = _run(helper_dir, event="workflow_dispatch", ref=ref, environment=environment, sha=OTHER_SHA)
    assert result.returncode == 0, result.stderr
    assert calls == [f"{helper} netbox-packer {OTHER_SHA}"]


def test_helper_failure_fails_the_run(helper_dir: Path) -> None:
    helper = helper_dir / PRODUCTION_HELPER
    helper.write_text('#!/usr/bin/env bash\necho "$*" >> "$CALL_LOG"\nexit 7\n', encoding="utf-8")
    result, calls = _run(helper_dir, event="workflow_dispatch", ref="refs/heads/main", environment="production")
    assert result.returncode == 7
    assert calls == [f"netbox-packer {SHA}"]


@pytest.mark.parametrize(("event", "ref", "requested_ref", "environment"), REFUSED)
def test_refused_combinations_call_no_helper(
    helper_dir: Path, event: str, ref: str, requested_ref: str, environment: str
) -> None:
    result, calls = _run(helper_dir, event=event, ref=ref, requested_ref=requested_ref, environment=environment)
    assert result.returncode != 0
    assert calls == []
    assert "ERROR:" in result.stderr


@pytest.mark.parametrize("sha", ["", "main", SHA[:12], SHA.upper(), SHA + "0"])
def test_invalid_run_commit_is_refused(helper_dir: Path, sha: str) -> None:
    result, calls = _run(
        helper_dir, event="workflow_dispatch", ref="refs/heads/main", environment="production", sha=sha
    )
    assert result.returncode != 0
    assert calls == []


@pytest.mark.parametrize(
    ("event", "ref", "environment", "missing"),
    [
        ("workflow_dispatch", "refs/heads/main", "production", PRODUCTION_HELPER),
        ("push", "refs/heads/develop", "", STAGING_HELPER),
    ],
)
def test_missing_helper_fails_without_fallback(
    helper_dir: Path, event: str, ref: str, environment: str, missing: str
) -> None:
    (helper_dir / missing).unlink()
    result, calls = _run(helper_dir, event=event, ref=ref, environment=environment)
    assert result.returncode != 0
    assert calls == []
    assert "not installed" in result.stderr
