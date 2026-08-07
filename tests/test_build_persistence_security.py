"""Contracts preventing ephemeral image credentials from reaching durable builds."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _load_security_module():
    path = ROOT / "netbox_packer" / "security.py"
    spec = importlib.util.spec_from_file_location("netbox_packer_persistence_security", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load security helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_security_migration():
    path = ROOT / "netbox_packer" / "migrations" / "0024_proxbox_write_gate_and_override_scrub.py"

    class Migration:
        pass

    class RunPython:
        noop = object()

        def __init__(self, *args, **kwargs):
            pass

    migrations = SimpleNamespace(
        Migration=Migration,
        AddField=lambda *_args, **_kwargs: None,
        AlterField=lambda *_args, **_kwargs: None,
        RunPython=RunPython,
    )
    models = SimpleNamespace(
        BooleanField=lambda *_args, **_kwargs: None,
        CharField=lambda *_args, **_kwargs: None,
        JSONField=lambda *_args, **_kwargs: None,
    )
    django = types.ModuleType("django")
    django_db = types.ModuleType("django.db")
    django_db.migrations = migrations
    django_db.models = models
    package = types.ModuleType("netbox_packer")
    package.__path__ = [str(ROOT / "netbox_packer")]
    app_models = types.ModuleType("netbox_packer.models")
    app_models.validate_packer_build_overrides = lambda _value: None
    package.models = app_models
    stubs = {
        "django": django,
        "django.db": django_db,
        "netbox_packer": package,
        "netbox_packer.models": app_models,
    }

    spec = importlib.util.spec_from_file_location("netbox_packer_security_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load security migration from {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


def test_signed_image_url_is_removed_without_reflection() -> None:
    security = _load_security_module()
    canary = "signed-image-canary-not-a-secret"
    overrides = {
        "image_url": f"https://images.example/image.qcow2?token={canary}",
        "ssh_host": "pve01.example",
    }

    assert security.contains_non_persistable_build_override(overrides) is True
    sanitized = security.redact_non_persistable_build_overrides(overrides)
    assert sanitized == {"ssh_host": "pve01.example"}
    assert canary not in repr(sanitized)

    legacy_log = (
        "[INFO] Cloud-init template image build for 'test'\n"
        f"[INFO] Base image: https://images.example/image.qcow2?token={canary}\n"
        f"[BUILD_SCRIPT]\ncurl 'https://images.example/image.qcow2?token={canary}'\n"
        f"[STDOUT]\n{canary}\n[STDERR]\n{canary}"
    )
    redacted_log = security.redact_legacy_proxbox_build_log(legacy_log)
    assert redacted_log == security.REDACTED_LEGACY_PROXBOX_LOG
    assert canary not in redacted_log


def test_nested_signed_image_urls_are_rejected_and_recursively_removed() -> None:
    security = _load_security_module()
    canary = "nested-signed-image-canary-not-a-secret"
    overrides = {
        "source": {"image_url": f"https://images.example/image.qcow2?token={canary}"},
        "layers": [{"name": "base"}, {"image_url": f"https://images.example/{canary}"}],
        "target_node": "pve01",
    }

    assert security.contains_non_persistable_build_override(overrides) is True
    sanitized = security.redact_non_persistable_build_overrides(overrides)
    assert sanitized == {
        "source": {},
        "layers": [{"name": "base"}, {}],
        "target_node": "pve01",
    }
    assert canary not in repr(sanitized)


def test_migration_recursively_scrubs_nested_signed_image_urls() -> None:
    migration = _load_security_migration()
    canary = "migration-nested-signed-image-canary-not-a-secret"
    row = SimpleNamespace(
        pk=17,
        variable_overrides={
            "source": {"image_url": f"https://images.example/image.qcow2?token={canary}"},
            "layers": [{"image_url": f"https://images.example/{canary}"}],
            "credentials": {"password": canary},
            "metadata": f"password: {canary}",
            "target_node": "pve01",
        },
        log="ordinary historical build log",
    )
    change = SimpleNamespace(
        pk=23,
        prechange_data={
            "status": "queued",
            "variable_overrides": {"source": {"image_url": f"https://images.example/image.qcow2?token={canary}"}},
            "log": f"legacy output {canary}",
        },
        postchange_data={
            "status": "running",
            "variable_overrides": {"target_node": "pve01"},
            "log": "ordinary output",
        },
    )
    build_updates: dict[int, dict] = {}
    change_updates: dict[int, dict] = {}

    class BuildManager:
        def only(self, *fields):
            return self

        def iterator(self):
            return iter((row,))

        def filter(self, *, pk):
            return SimpleNamespace(update=lambda **values: build_updates.__setitem__(pk, values))

    class ChangeQuery:
        def only(self, *fields):
            return self

        def iterator(self):
            return iter((change,))

    class ChangeManager:
        def filter(self, **criteria):
            if "pk" in criteria:
                pk = criteria["pk"]
                return SimpleNamespace(update=lambda **values: change_updates.__setitem__(pk, values))
            assert criteria == {
                "changed_object_type__app_label": "netbox_packer",
                "changed_object_type__model": "packerbuild",
            }
            return ChangeQuery()

    historical_build = SimpleNamespace(objects=BuildManager())
    historical_change = SimpleNamespace(objects=ChangeManager())

    def get_model(app_label, model_name):
        models = {
            ("netbox_packer", "PackerBuild"): historical_build,
            ("core", "ObjectChange"): historical_change,
        }
        return models[(app_label, model_name)]

    apps = SimpleNamespace(get_model=get_model)

    migration.scrub_legacy_proxbox_secrets(apps, schema_editor=None)

    assert build_updates[17]["variable_overrides"] == {
        "source": {},
        "layers": [{}],
        "metadata": "[REDACTED]",
        "target_node": "pve01",
    }
    assert build_updates[17]["log"] == migration._REDACTED_PACKER_LOG
    assert change_updates[23] == {
        "prechange_data": {"status": "queued"},
        "postchange_data": {"status": "running"},
    }
    assert canary not in repr(build_updates[17])
    assert canary not in repr(change_updates[23])


def test_all_durable_boundaries_reject_or_redact_image_url() -> None:
    models = _read("netbox_packer/models.py")
    serializers = _read("netbox_packer/api/serializers.py")
    api_views = _read("netbox_packer/api/views.py")
    jobs = _read("netbox_packer/jobs.py")
    graphql_types = _read("netbox_packer/graphql/types.py")
    build_template = _read("netbox_packer/templates/netbox_packer/packerbuild.html")
    migration = _read("netbox_packer/migrations/0024_proxbox_write_gate_and_override_scrub.py")

    assert "validate_packer_build_overrides(self.variable_overrides)" in models
    assert "def safe_variable_overrides(self)" in models
    assert "def safe_log(self)" in models
    assert "def serialize_object(self, exclude=None)" in models
    assert 'excluded_fields.update(("variable_overrides", "log"))' in models
    assert 'representation["variable_overrides"] = instance.safe_variable_overrides' in serializers
    assert 'representation["log"] = instance.safe_log' in serializers
    assert '"log",' in serializers
    assert '"variable_overrides",' in serializers
    assert "read_only_fields = tuple(sorted(_PACKER_BUILD_PUBLIC_IMMUTABLE_FIELDS))" in serializers
    assert "This worker-managed field cannot be submitted." in serializers
    assert "PackerTemplateBuildRequestSerializer(data=request.data)" in api_views
    assert "contains_non_persistable_build_override(build.variable_overrides)" in jobs
    assert "_mark_build_failed_closed(build, template, message)" in jobs
    assert 'build.log = f"[ERROR] {message}"' in jobs
    assert "_resolve_cloud_image_url(template, build.variable_overrides)" not in jobs
    assert "_redact_non_persistable_overrides(overrides)" in migration
    assert "_omit_packer_build_snapshot_fields(snapshot)" in migration
    assert 'apps.get_model("core", "ObjectChange")' in migration
    assert 'update["log"] = _REDACTED_PACKER_LOG' in migration
    assert '"[BUILD_SCRIPT]"' in migration
    assert "print(" not in migration
    assert '@strawberry_django.type(models.PackerBuild, exclude=("variable_overrides", "log"))' in graphql_types
    assert 'models.PackerBuild, fields="__all__", exclude=' not in graphql_types
    assert "object.safe_log" in build_template
    assert "object.log" not in build_template


def test_proxbox_write_gate_covers_dispatch_worker_and_synchronous_provision() -> None:
    models = _read("netbox_packer/models.py")
    api_views = _read("netbox_packer/api/views.py")
    html_views = _read("netbox_packer/views.py")
    jobs = _read("netbox_packer/jobs.py")

    assert "proxbox_writes_enabled = models.BooleanField(" in models
    assert "default=False" in models
    assert "PackerPluginSettings.get_solo().proxbox_writes_enabled" in api_views
    assert "Cloud image builds require an explicit proxbox endpoint and node" in html_views
    assert html_views.count("PackerPluginSettings.get_solo().proxbox_writes_enabled") >= 1
    assert "PackerPluginSettings.get_solo().proxbox_writes_enabled" in jobs
    assert "if not settings_row.proxbox_writes_enabled:" in jobs


def test_security_migrations_extend_the_main_chain() -> None:
    metadata_migration = _read("netbox_packer/migrations/0023_alter_packerpluginsettings_proxbox_api_url.py")
    security_migration = _read("netbox_packer/migrations/0024_proxbox_write_gate_and_override_scrub.py")
    configuration = _read("docs/configuration.md")

    assert '("netbox_packer", "0022_update_fileserver_package_settings_comment")' in metadata_migration
    assert '("netbox_packer", "0023_alter_packerpluginsettings_proxbox_api_url")' in security_migration
    assert "python manage.py migrate netbox_packer 0022" in configuration
    assert "python manage.py migrate netbox_packer 0014" not in configuration


def test_legacy_preflight_and_service_restart_rollout_are_executable_as_documented() -> None:
    configuration = _read("docs/configuration.md")
    claude = _read("CLAUDE.md")
    workflow = _read(".gitea/workflows/deploy-production.yml")
    normalized_configuration = " ".join(configuration.split())
    stage_one = configuration.split("### Stage 2:", maxsplit=1)[0]

    assert "from netbox_packer.proxbox_client" not in stage_one
    assert "settings_row.proxbox_api_url = canonical_url" in stage_one
    assert "APPLY_CANONICAL_UPDATE = False" in stage_one
    assert 'normalize_proxbox_api_base_url("https://proxbox-api.internal.example:8000")' not in configuration
    assert "`netbox-service-guard-v1`" in normalized_configuration
    assert "netbox-staging-rq.service" in workflow
    assert "netbox-staging.service" in workflow
    assert "netbox.service" in workflow
    assert "netbox-rq.service" in workflow
    assert "guard_contract=netbox-service-guard-v1" in workflow
    assert '"$deploy_helper" --supports "$guard_contract"' in workflow
    assert 'NMC_NETBOX_DEPLOY_REQUIRE_SERVICE_GUARD="$guard_contract"' in workflow
    assert 'NMC_NETBOX_DEPLOY_WEB_UNIT="$web_unit"' in workflow
    assert 'NMC_NETBOX_DEPLOY_WORKER_UNIT="$worker_unit"' in workflow
    assert '"$deploy_helper" netbox-packer "$REF"' in workflow
    assert workflow.index('"$deploy_helper" --supports "$guard_contract"') < workflow.index(
        '"$deploy_helper" netbox-packer "$REF"'
    )
    assert "systemctl stop" not in workflow
    assert "systemctl mask" not in workflow
    assert "systemctl restart" not in workflow
    assert "systemctl show" not in workflow
    assert "nmc-prod-207" not in workflow
    assert "falls back to `ssh nmc-prod-207" not in claude
    assert "ssh nmc-prod-207 -- deploy-plugin" not in claude
    assert "Direct helper or SSH deployment is not an alternate path" in claude
    assert "proxbox_writes_enabled=False" in normalized_configuration
    rollback = configuration.split("### Rollback", maxsplit=1)[1]
    assert "Stop and runtime-mask" in rollback
    assert "netbox-staging.service" in rollback
    assert "netbox-staging-rq.service" in rollback
    assert "netbox.service" in rollback
    assert "netbox-production.service" in rollback
    assert "netbox-rq.service" in rollback
    assert rollback.index("migrate netbox_packer 0022") < rollback.index("restore the prior plugin package")
    assert "leave web and RQ stopped" in rollback
