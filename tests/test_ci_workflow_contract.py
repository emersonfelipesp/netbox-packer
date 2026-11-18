"""Keep complete public quality and generated screenshots behind normal review."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ["LINT_RESULT", "STATIC_RESULT", "PACKAGE_RESULT", "NETBOX_RESULT"]


def workflow(name: str) -> dict:
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_original_branch_admission_and_independent_main_runs() -> None:
    ci = workflow("ci.yml")
    assert ci["on"]["push"]["branches"] == ["main", "v0.0.2"]
    assert ci["on"]["pull_request"]["branches"] == ["main", "v0.0.2"]
    for name in ["e2e.yml", "docs.yml", "page-coverage.yml"]:
        document = workflow(name)
        assert document["on"]["push"]["branches"] == ["main", "develop"]
        assert document["on"]["pull_request"]["branches"] == ["main", "develop"]
    for name in ["ci.yml", "e2e.yml", "docs.yml", "page-coverage.yml"]:
        concurrency = workflow(name)["concurrency"]
        assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
        assert "github.event.pull_request.number" in concurrency["group"]
        assert "github.run_id" in concurrency["group"]


def test_source_quality_keeps_every_original_fast_producer() -> None:
    jobs = workflow("ci.yml")["jobs"]
    assert set(jobs) == {"lint", "static-tests", "package", "test", "complete"}
    assert jobs["lint"]["steps"][2]["run"] == "pip install ruff==0.15.8"
    assert jobs["lint"]["steps"][3]["run"] == "ruff check netbox_packer/"
    assert jobs["lint"]["steps"][4]["run"] == "ruff format --check netbox_packer/"
    assert jobs["static-tests"]["steps"][-1]["run"] == "pytest tests/ -v"
    assert "python -m build" in jobs["package"]["steps"][-1]["run"]
    assert "python -m twine check dist/*" in jobs["package"]["steps"][-1]["run"]


def test_source_quality_keeps_original_netbox_and_service_versions() -> None:
    source = workflow("ci.yml")["jobs"]["test"]
    assert source["needs"] == ["lint", "static-tests", "package"]
    assert source["steps"][1]["with"]["python-version"] == "3.13"
    assert source["services"]["postgres"]["image"] == "postgres:16-alpine"
    assert source["services"]["redis"]["image"] == "redis:7-alpine"
    assert source["steps"][-1]["env"]["NETBOX_REF"] == "v4.6.1"
    assert source["steps"][-1]["env"]["TEST_LABELS"] == "netbox_packer"
    assert source["steps"][-1]["run"] == "bash .github/ci/netbox46-source-test.sh"


def test_docker_smoke_keeps_both_original_images_and_python() -> None:
    job = workflow("e2e.yml")["jobs"]["e2e-docker"]
    assert job["strategy"]["matrix"]["netbox_image"] == [
        "netboxcommunity/netbox:v4.5.8",
        "netboxcommunity/netbox:v4.6.1",
    ]
    assert job["strategy"]["fail-fast"] == "false"
    assert job["steps"][1]["with"]["python-version"] == "3.12"
    assert "docker pull" in job["steps"][3]["run"]
    assert "import netbox_packer" in job["steps"][3]["run"]


def test_complete_ci_requires_every_original_producer() -> None:
    job = workflow("ci.yml")["jobs"]["complete"]
    assert job["if"] == "always()"
    assert job["needs"] == ["lint", "static-tests", "package", "test"]
    step = job["steps"][0]
    assert set(step["env"]) == set(RESULTS)
    assert step["run"] == "".join(f'test "${name}" = success\n' for name in RESULTS)
    result = subprocess.run(
        ["bash", "-e"],
        input=step["run"],
        text=True,
        env={**os.environ, **dict.fromkeys(RESULTS, "success")},
        check=False,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("producer", RESULTS)
@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", ""])
def test_complete_ci_rejects_each_unsuccessful_producer(producer: str, state: str) -> None:
    command = workflow("ci.yml")["jobs"]["complete"]["steps"][0]["run"]
    assert command == "".join(f'test "${name}" = success\n' for name in RESULTS)
    results = {**dict.fromkeys(RESULTS, "success"), producer: state}
    result = subprocess.run(["bash", "-e"], input=command, text=True, env={**os.environ, **results}, check=False)
    assert result.returncode != 0


@pytest.mark.parametrize("state,expected", [("success", 0), ("failure", 1), ("cancelled", 1), ("skipped", 1)])
def test_complete_e2e_requires_the_whole_original_matrix(state: str, expected: int) -> None:
    job = workflow("e2e.yml")["jobs"]["complete"]
    assert job["if"] == "always()"
    assert job["needs"] == ["e2e-docker"]
    command = job["steps"][0]["run"]
    assert command == 'test "$E2E_RESULT" = success'
    result = subprocess.run(
        ["bash", "-e"], input=command, text=True, env={**os.environ, "E2E_RESULT": state}, check=False
    )
    assert result.returncode == expected


def test_screenshot_generation_keeps_manual_and_tag_admission_without_write_authority() -> None:
    document = workflow("docs-screenshots.yml")
    assert set(document["on"]) == {"workflow_dispatch", "push"}
    assert document["on"]["push"]["tags"] == ["v*"]
    assert document["permissions"] == {"contents": "read"}
    job = document["jobs"]["capture-screenshots"]
    assert job["timeout-minutes"] == "60"
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] == "false"
    assert "token" not in steps[0]["with"]


def test_screenshot_generation_keeps_validation_without_source_or_pages_writes() -> None:
    steps = workflow("docs-screenshots.yml")["jobs"]["capture-screenshots"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "python scripts/capture_screenshots.py" in commands
    assert "mkdocs build --strict" in commands
    for forbidden in ["git commit", "git push", "git pull", "ghp-import"]:
        assert forbidden not in commands


def test_screenshot_artifact_is_required_and_unique_to_the_original_run_attempt() -> None:
    steps = workflow("docs-screenshots.yml")["jobs"]["capture-screenshots"]["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["path"] == "docs/assets/screenshots/"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "github.run_id" in upload["with"]["name"]
    assert "github.run_attempt" in upload["with"]["name"]


def test_only_merged_main_docs_publish_outside_explicit_qualification() -> None:
    steps = workflow("docs.yml")["jobs"]["build-and-deploy"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build docs")
    assert build["run"] == "mkdocs build --strict"
    assert "if" not in build
    deploy = next(step for step in steps if step.get("name") == "Deploy to gh-pages")
    assert deploy["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main' && vars.CI_QUALIFICATION_ONLY != 'true'"
    )
    assert "ghp-import -n -p -f" in deploy["run"]
