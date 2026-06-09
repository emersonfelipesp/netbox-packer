"""Static structure tests — run without Django or NetBox installed."""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "netbox_packer"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _class_block(src: str, class_name: str) -> str:
    start = src.find(f"class {class_name}")
    assert start >= 0, f"{class_name} not found in source"
    end = src.find("\nclass ", start + 1)
    return src[start : end if end > 0 else None]


def test_all_python_files_parse() -> None:
    """Every .py file in the package must parse without syntax errors."""
    errors: list[str] = []
    for path in PKG.rglob("*.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    assert not errors, "Syntax errors:\n" + "\n".join(errors)


def test_pyproject_metadata() -> None:
    """pyproject.toml must declare expected metadata."""
    data = tomllib.loads(_read("pyproject.toml"))
    project = data["project"]
    assert project["name"] == "netbox-packer"
    assert project["version"] == "0.0.4"
    assert project["requires-python"] >= ">=3.12"
    assert "setuptools" in data["build-system"]["requires"][0]
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert "License :: OSI Approved :: Apache Software License" not in project["classifiers"]
    assert project["urls"]["Documentation"] == "https://emersonfelipesp.github.io/netbox-packer/"
    assert (ROOT / "LICENSE").is_file()


def test_plugin_config_fields() -> None:
    """PluginConfig in __init__.py must declare correct name and base_url.

    The version string must stay in sync with pyproject.toml.
    """
    data = tomllib.loads(_read("pyproject.toml"))
    pyproject_version = data["project"]["version"]
    src = _read("netbox_packer/__init__.py")
    assert 'name = "netbox_packer"' in src
    assert 'base_url = "packer"' in src
    assert f'version = "{pyproject_version}"' in src
    assert 'min_version = "4.5.8"' in src
    assert 'max_version = "4.6.99"' in src
    assert "def ready" in src and "jobs" in src  # jobs module imported in ready() for RQ discovery


def test_build_status_choices_include_stale() -> None:
    """BuildStatusChoices must include all expected statuses including stale."""
    choices_src = _read("netbox_packer/choices.py")
    block = _class_block(choices_src, "BuildStatusChoices")
    for status in ("pending", "building", "ready", "failed", "deprecated", "stale"):
        assert f'"{status}"' in block, f"Missing status '{status}' in BuildStatusChoices"


def test_os_family_choices() -> None:
    choices_src = _read("netbox_packer/choices.py")
    block = _class_block(choices_src, "OSFamilyChoices")
    for family in ("ubuntu", "rhel", "debian"):
        assert f'"{family}"' in block, f"Missing os_family '{family}'"


def test_packer_template_model_fields() -> None:
    """PackerTemplate model must declare expected fields."""
    models_src = _read("netbox_packer/models.py")
    block = _class_block(models_src, "PackerTemplate")
    for field in (
        "name",
        "os_family",
        "os_version",
        "proxmox_template_id",
        "proxmox_node",
        "cloud_init_ready",
        "min_cpu_type",
        "build_status",
        "built_at",
        "max_age_days",
        "auto_rebuild",
        "hcp_bucket_name",
        "installer_config",
        "installer_config_checksum_at_build",
    ):
        assert field in block, f"Missing field '{field}' in PackerTemplate"


def test_packer_template_computed_properties() -> None:
    """PackerTemplate must have age_days, is_stale, and derived_vms properties."""
    models_src = _read("netbox_packer/models.py")
    block = _class_block(models_src, "PackerTemplate")
    for prop in ("age_days", "is_stale", "derived_vms"):
        assert f"def {prop}" in block, f"Missing property '{prop}' on PackerTemplate"


def test_packer_installer_config_checksum_in_save() -> None:
    """PackerInstallerConfig.save() must compute sha256 checksum."""
    models_src = _read("netbox_packer/models.py")
    block = _class_block(models_src, "PackerInstallerConfig")
    assert "def save" in block
    assert "sha256" in block
    assert "checksum" in block


def test_packer_build_target_model_fields() -> None:
    models_src = _read("netbox_packer/models.py")
    block = _class_block(models_src, "PackerBuildTarget")
    for field in ("template", "proxmox_node", "priority", "enabled"):
        assert field in block, f"Missing field '{field}' in PackerBuildTarget"


def test_api_routes_registered() -> None:
    """API router must register all four viewsets."""
    api_urls = _read("netbox_packer/api/urls.py")
    for route in ("packer-templates", "build-jobs", "installer-configs", "build-targets"):
        assert f'"{route}"' in api_urls, f"Missing route '{route}' in api/urls.py"


def test_build_action_endpoint_exists() -> None:
    """POST /build/ action and cancel action must exist in api/views.py."""
    api_views = _read("netbox_packer/api/views.py")
    assert 'def build' in api_views
    assert 'def cancel' in api_views
    assert 'HTTP_202_ACCEPTED' in api_views


def test_validate_node_uses_node_affinity_validator() -> None:
    """validate_node action must use NodeAffinityValidator."""
    api_views = _read("netbox_packer/api/views.py")
    assert "NodeAffinityValidator" in api_views
    assert "validate_node" in api_views


def test_node_affinity_validator_has_three_checks() -> None:
    """NodeAffinityValidator must implement node, cpu, and storage checks."""
    validators_src = _read("netbox_packer/validators.py")
    for method in ("_check_node_exists", "_check_cpu_type", "_check_storage_pool"):
        assert f"def {method}" in validators_src, f"Missing method '{method}' in NodeAffinityValidator"


def test_jobs_has_packer_build_job() -> None:
    jobs_src = _read("netbox_packer/jobs.py")
    assert "class PackerBuildJob" in jobs_src
    assert "class PackerStalenessCheckJob" in jobs_src
    assert "def select_build_node" in jobs_src
    assert "NodeAffinityValidator" in jobs_src


def test_hcp_sync_idempotent_guard() -> None:
    """hcp_sync must skip sync when iteration_id is unchanged."""
    hcp_src = _read("netbox_packer/hcp_sync.py")
    assert "_is_hcp_configured" in hcp_src
    assert "iteration_id == iteration_id" in hcp_src or "hcp_iteration_id == iteration_id" in hcp_src
    assert "HCP_CLIENT_ID" in hcp_src


def test_vm_lineage_migration_exists() -> None:
    """Migration 0002 must create source_packer_template custom field."""
    migration_src = _read("netbox_packer/migrations/0002_vm_lineage_custom_field.py")
    assert "source_packer_template" in migration_src
    assert "get_or_create" in migration_src
    assert "virtualization" in migration_src


def test_management_commands_exist() -> None:
    """Both management commands must exist."""
    cmd1 = _read("netbox_packer/management/commands/check_packer_staleness.py")
    cmd2 = _read("netbox_packer/management/commands/import_installer_config.py")
    assert "class Command" in cmd1
    assert "class Command" in cmd2
    assert "--dry-run" in cmd1
    assert "--os-family" in cmd2


def test_navigation_has_two_groups() -> None:
    """Navigation must register Template and Build menu groups."""
    nav_src = _read("netbox_packer/navigation.py")
    for label in ("Templates", "Builds"):
        assert label in nav_src, f"Missing navigation group '{label}'"


def test_search_registers_packer_template() -> None:
    """Search module must register PackerTemplate."""
    search_src = _read("netbox_packer/search.py")
    assert "PackerTemplate" in search_src


def test_ci_workflow_exists() -> None:
    """CI workflow must exist with expected jobs."""
    ci = _read(".github/workflows/ci.yml")
    assert "ruff check" in ci
    assert "ruff format" in ci
    assert "pytest tests" in ci
