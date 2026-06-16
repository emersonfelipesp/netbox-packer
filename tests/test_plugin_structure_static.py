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


def test_migration_0011_k8s_role_templates_exists() -> None:
    """Migration 0011 must seed K8s Control Plane (VMID 9013) and Worker (VMID 9014) templates."""
    src = _read("netbox_packer/migrations/0011_seed_k8s_role_templates.py")

    # Both template names must be present
    assert "k8s-1.31-control-plane-ubuntu-2404" in src, "Missing CP template name"
    assert "k8s-1.31-worker-node-ubuntu-2404" in src, "Missing Worker template name"

    # Both installer config names must be present
    assert "k8s-1.31-control-plane-cloud-config" in src, "Missing CP config name"
    assert "k8s-1.31-worker-node-cloud-config" in src, "Missing Worker config name"

    # VMIDs must be pinned to 9013 (CP) and 9014 (Worker)
    assert "9013" in src, "Missing CP VMID 9013"
    assert "9014" in src, "Missing Worker VMID 9014"

    # Must target the production endpoint, NOT the development one
    assert "10.0.30.71" in src, "Missing production endpoint"
    assert "10.0.30.139" not in src, "Development endpoint must not appear in migration 0011"

    # Dependency must chain from 0010_alter_packertemplate_zabbix_server
    dep = '"netbox_packer", "0010_alter_packertemplate_zabbix_server"'
    assert dep in src, "Missing dependency on 0010_alter_packertemplate_zabbix_server"

    # Must use get_or_create for idempotency
    assert src.count("get_or_create") >= 4, "Expected at least 4 get_or_create calls (2 configs + 2 templates)"

    # Reverse migration must be present (even as no-op)
    assert "def unseed_k8s_role_templates" in src, "Missing reverse migration function"

    # Both cloud-configs must be syntactically valid Python strings (checked by test_all_python_files_parse)
    assert "#cloud-config" in src, "Missing #cloud-config marker"

    # CP template must pre-pull control-plane images; Worker must not
    assert "kubeadm config images pull" in src, "CP template must pre-pull control-plane images"
    assert "k8s-control-plane-bootstrap complete" in src, "Missing CP bootstrap completion marker"
    assert "k8s-worker-node-bootstrap complete" in src, "Missing Worker bootstrap completion marker"


def test_migration_0008_monitoring_agent_fields_exists() -> None:
    """Migration 0008 must add install_qemu_guest_agent, install_zabbix_agent2, and zabbix_server fields."""
    src = _read("netbox_packer/migrations/0008_packertemplate_monitoring_agents.py")

    # All three fields must be added
    assert "install_qemu_guest_agent" in src, "Missing install_qemu_guest_agent field"
    assert "install_zabbix_agent2" in src, "Missing install_zabbix_agent2 field"
    assert "zabbix_server" in src, "Missing zabbix_server field"

    # Dependency must chain from 0007
    assert '"netbox_packer", "0007_seed_influxdb_cloud_init"' in src, "Missing dependency on 0007"

    # Defaults must be correct
    assert "default=True" in src, "install_qemu_guest_agent/install_zabbix_agent2 must default to True"
    assert "zabbix.nmulti.cloud" in src, "zabbix_server must default to zabbix.nmulti.cloud"


def test_migration_0009_kubernetes_seed_exists() -> None:
    """Migration 0009 must seed the Kubernetes 1.31 node template (VMID 9012) on 10.0.30.71."""
    src = _read("netbox_packer/migrations/0009_seed_kubernetes_cloud_init.py")

    # Template and config names must be present
    assert "k8s-1.31-ubuntu-2404-node" in src, "Missing K8s node template name"

    # VMID must be pinned to 9012
    assert "9012" in src, "Missing node VMID 9012"

    # Must target production endpoint
    assert "10.0.30.71" in src, "Missing production endpoint"
    assert "10.0.30.139" not in src, "Development endpoint must not appear in migration 0009"

    # Dependency must chain from 0008
    assert '"netbox_packer", "0008_packertemplate_monitoring_agents"' in src, "Missing dependency on 0008"

    # Must use get_or_create for idempotency
    assert src.count("get_or_create") >= 2, "Expected at least 2 get_or_create calls (config + template)"

    # Reverse migration must be a no-op
    assert "def unseed_kubernetes" in src, "Missing reverse migration function"

    # Cloud-config marker
    assert "#cloud-config" in src, "Missing #cloud-config marker"


def test_migration_0012_powerdns_seed_exists() -> None:
    """Migration 0012 must seed PowerDNS Authoritative (VMID 9017) and Recursor (VMID 9018) templates."""
    src = _read("netbox_packer/migrations/0012_seed_powerdns_cloud_init.py")

    # Both template names must be present
    assert "pdns-auth-ubuntu-2404" in src, "Missing pdns-auth template name"
    assert "pdns-recursor-ubuntu-2404" in src, "Missing pdns-recursor template name"

    # VMIDs must be pinned
    assert "9017" in src, "Missing PowerDNS Auth VMID 9017"
    assert "9018" in src, "Missing PowerDNS Recursor VMID 9018"

    # Must target production endpoint, not development
    assert "10.0.30.71" in src, "Missing production endpoint"
    assert "10.0.30.139" not in src, "Development endpoint must not appear in migration 0012"

    # DNS domain and nameservers
    assert "nmulti.cloud" in src, "Missing DNS domain nmulti.cloud"
    assert "168.0.96.26" in src, "Missing nameserver 168.0.96.26"
    assert "168.0.96.27" in src, "Missing nameserver 168.0.96.27"

    # Cloud-config strings must NOT contain 'zabbix-agent2' (injected by _inject_monitoring_agents)
    assert "zabbix-agent2" not in src, (
        "Cloud-config must NOT contain 'zabbix-agent2' — injection is handled by _inject_monitoring_agents"
    )

    # Official PowerDNS APT repo must be referenced
    assert "repo.powerdns.com" in src, "Missing PowerDNS APT repository"
    assert "noble-auth-49" in src, "Missing PowerDNS Authoritative 4.9 APT suite"
    assert "noble-rec-51" in src, "Missing PowerDNS Recursor 5.1 APT suite"

    # Dependency must chain from 0011_seed_k8s_role_templates
    dep = '"netbox_packer", "0011_seed_k8s_role_templates"'
    assert dep in src, "Missing dependency on 0011_seed_k8s_role_templates"

    # Must use get_or_create for idempotency
    assert src.count("get_or_create") >= 4, "Expected at least 4 get_or_create calls (2 configs + 2 templates)"

    # Reverse migration must be a no-op
    assert "def unseed_powerdns" in src, "Missing reverse migration function"

    # Both cloud-configs must be valid Python strings (checked by test_all_python_files_parse)
    assert src.count("#cloud-config") >= 2, "Expected #cloud-config marker in both configs"
