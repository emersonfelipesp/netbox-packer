"""Behavioral tests for build dispatch without a full NetBox runtime."""

from __future__ import annotations

import importlib
import sys
import time
import types
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "netbox_packer"
ISOLATED_PREFIXES = (
    "django",
    "netbox",
    "netbox_packer",
    "rest_framework",
    "utilities",
)


@pytest.fixture
def isolated_imports():
    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in ISOLATED_PREFIXES)
    }
    for name in saved:
        sys.modules.pop(name, None)

    yield

    for name in list(sys.modules):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in ISOLATED_PREFIXES):
            sys.modules.pop(name, None)
    sys.modules.update(saved)


def _install_package() -> None:
    package = types.ModuleType("netbox_packer")
    package.__path__ = [str(PKG)]
    sys.modules["netbox_packer"] = package


def _install_jobs_import_stubs() -> None:
    _install_package()

    conf = types.ModuleType("django.conf")
    conf.settings = SimpleNamespace(
        PLUGINS_CONFIG={"netbox_packer": {}},
        SECRET_KEY="test-secret",
    )

    timezone_mod = types.ModuleType("django.utils.timezone")
    timezone_mod.now = Mock(return_value=datetime(2026, 1, 1, tzinfo=UTC))
    django_utils = types.ModuleType("django.utils")
    django_utils.timezone = timezone_mod

    jobs_mod = types.ModuleType("netbox.jobs")

    class JobRunner:
        pass

    jobs_mod.JobRunner = JobRunner

    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.conf"] = conf
    sys.modules["django.utils"] = django_utils
    sys.modules["django.utils.timezone"] = timezone_mod
    sys.modules["netbox"] = types.ModuleType("netbox")
    sys.modules["netbox.jobs"] = jobs_mod


def _import_jobs_module():
    _install_jobs_import_stubs()
    return importlib.import_module("netbox_packer.jobs")


def _import_api_serializers_module():
    _install_package()

    class Field:
        def __init__(self, *args, **kwargs):
            pass

    class Serializer:
        def __init__(self, *args, **kwargs):
            pass

    netbox_serializers = types.ModuleType("netbox.api.serializers")
    netbox_serializers.NetBoxModelSerializer = Serializer
    rest_serializers = types.ModuleType("rest_framework.serializers")
    rest_serializers.Serializer = Serializer
    rest_serializers.BooleanField = Field
    rest_serializers.JSONField = Field
    rest_serializers.HyperlinkedIdentityField = Field
    rest_serializers.ValidationError = ValueError
    rest_framework = types.ModuleType("rest_framework")
    rest_framework.serializers = rest_serializers
    models = types.ModuleType("netbox_packer.models")
    for name in ("PackerBuild", "PackerBuildTarget", "PackerInstallerConfig", "PackerTemplate"):
        setattr(models, name, type(name, (), {}))

    sys.modules["netbox"] = types.ModuleType("netbox")
    sys.modules["netbox.api"] = types.ModuleType("netbox.api")
    sys.modules["netbox.api.serializers"] = netbox_serializers
    sys.modules["rest_framework"] = rest_framework
    sys.modules["rest_framework.serializers"] = rest_serializers
    sys.modules["netbox_packer.models"] = models
    return importlib.import_module("netbox_packer.api.serializers")


def _import_models_module():
    _install_package()

    class ValidationError(Exception):
        pass

    class Validator:
        def __init__(self, *args, **kwargs):
            pass

    class NetBoxModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def clean(self):
            pass

        def save(self, *args, **kwargs):
            pass

    fields = types.ModuleType("django.db.models")

    def field(*_args, **_kwargs):
        return None

    for name in (
        "BooleanField",
        "CharField",
        "DateTimeField",
        "ForeignKey",
        "IntegerField",
        "JSONField",
        "PositiveIntegerField",
        "TextField",
        "URLField",
        "UniqueConstraint",
    ):
        setattr(fields, name, field)
    fields.CASCADE = object()
    fields.SET_NULL = object()

    django_db = types.ModuleType("django.db")
    django_db.models = fields
    django_exceptions = types.ModuleType("django.core.exceptions")
    django_exceptions.ValidationError = ValidationError
    django_validators = types.ModuleType("django.core.validators")
    django_validators.RegexValidator = Validator
    django_validators.URLValidator = Validator
    netbox_models = types.ModuleType("netbox.models")
    netbox_models.NetBoxModel = NetBoxModel

    class BuildStatusChoices:
        CHOICE_PENDING = "pending"
        CHOICE_BUILDING = "building"
        CHOICE_READY = "ready"
        CHOICE_FAILED = "failed"
        CHOICE_DEPRECATED = "deprecated"
        CHOICE_STALE = "stale"

    choices = types.ModuleType("netbox_packer.choices")
    choices.BuildStatusChoices = BuildStatusChoices
    for name in ("OSFamilyChoices", "StorageFormatChoices", "StoragePoolTypeChoices"):
        setattr(choices, name, type(name, (), {}))

    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.core"] = types.ModuleType("django.core")
    sys.modules["django.core.exceptions"] = django_exceptions
    sys.modules["django.core.validators"] = django_validators
    sys.modules["django.db"] = django_db
    sys.modules["django.db.models"] = fields
    sys.modules["netbox"] = types.ModuleType("netbox")
    sys.modules["netbox.models"] = netbox_models
    sys.modules["netbox_packer.choices"] = choices
    return importlib.import_module("netbox_packer.models")


def test_dispatch_build_enqueues_with_build_id_keyword(isolated_imports) -> None:
    jobs = _import_jobs_module()
    enqueue = Mock()
    jobs.PackerBuildJob.enqueue = enqueue

    jobs.dispatch_build(SimpleNamespace(pk=123))

    enqueue.assert_called_once_with(build_id=123)
    assert "instance" not in enqueue.call_args.kwargs


def test_dispatch_authorizes_cloud_build_before_enqueue(isolated_imports) -> None:
    jobs = _import_jobs_module()
    calls: list[str] = []
    jobs._authorize_cloud_build_dispatch = lambda build: calls.append(f"authorize:{build.pk}")
    jobs.PackerBuildJob.enqueue = lambda **kwargs: calls.append(f"enqueue:{kwargs['build_id']}")
    build = SimpleNamespace(
        pk=124,
        template=SimpleNamespace(installer_config=SimpleNamespace(installer_type="cloud_config")),
    )

    jobs.dispatch_build(build)

    assert calls == ["authorize:124", "enqueue:124"]


def test_dispatch_authorizes_every_possible_enabled_cloud_build_target(
    isolated_imports,
) -> None:
    jobs = _import_jobs_module()

    class Targets:
        def all(self):
            return self

        def order_by(self, field):
            assert field == "priority"
            return [
                SimpleNamespace(enabled=True, proxmox_endpoint="https://pve-a.example:8006"),
                SimpleNamespace(enabled=True, proxmox_endpoint="https://pve-b.example:8006"),
            ]

    build = SimpleNamespace(
        variable_overrides={},
        template=SimpleNamespace(
            proxmox_endpoint="",
            build_targets=Targets(),
        ),
    )

    assert jobs._endpoint_urls_for_cloud_dispatch(build) == [
        "https://pve-a.example:8006",
        "https://pve-b.example:8006",
    ]


def test_cloud_dispatch_refuses_all_disabled_configured_targets(isolated_imports) -> None:
    jobs = _import_jobs_module()

    class Targets:
        def all(self):
            return self

        def order_by(self, field):
            assert field == "priority"
            return [SimpleNamespace(enabled=False)]

    build = SimpleNamespace(
        variable_overrides={},
        template=SimpleNamespace(
            name="cloud-template",
            proxmox_endpoint="https://primary.example:8006",
            build_targets=Targets(),
        ),
    )

    with pytest.raises(RuntimeError, match="No enabled PackerBuildTarget"):
        jobs._endpoint_urls_for_cloud_dispatch(build)


def test_cloud_dispatch_authorizes_each_possible_target_with_configured_backend(
    isolated_imports,
) -> None:
    jobs = _import_jobs_module()
    calls = []

    class Targets:
        def all(self):
            return self

        def order_by(self, field):
            return [
                SimpleNamespace(enabled=True, proxmox_endpoint="https://pve-a.example:8006"),
                SimpleNamespace(enabled=True, proxmox_endpoint="https://pve-b.example:8006"),
            ]

    settings_row = SimpleNamespace(proxbox_api_url="https://proxbox-api.example")
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerPluginSettings = type(
        "PackerPluginSettings",
        (),
        {"get_solo": staticmethod(lambda: settings_row)},
    )
    sys.modules["netbox_packer.models"] = models_mod
    jobs._authorize_selected_endpoint = lambda endpoint, backend: calls.append((endpoint, backend))
    build = SimpleNamespace(
        variable_overrides={},
        template=SimpleNamespace(
            name="cloud-template",
            installer_config=SimpleNamespace(installer_type="cloud_config"),
            build_targets=Targets(),
        ),
    )

    jobs._authorize_cloud_build_dispatch(build)

    assert calls == [
        ("https://pve-a.example:8006", "https://proxbox-api.example"),
        ("https://pve-b.example:8006", "https://proxbox-api.example"),
    ]


@pytest.mark.parametrize("has_targets", (False, True))
def test_cloud_selection_distinguishes_no_targets_from_all_disabled(
    isolated_imports,
    has_targets: bool,
) -> None:
    jobs = _import_jobs_module()

    class Targets:
        def all(self):
            return self

        def order_by(self, field):
            return [SimpleNamespace(enabled=False)] if has_targets else []

    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerBuild = type("PackerBuild", (), {})
    sys.modules["netbox_packer.models"] = models_mod
    validators_mod = types.ModuleType("netbox_packer.validators")
    validators_mod.NodeAffinityValidator = object
    sys.modules["netbox_packer.validators"] = validators_mod
    template = SimpleNamespace(
        name="cloud-template",
        proxmox_endpoint="https://primary.example:8006",
        proxmox_node="pve-primary",
        build_targets=Targets(),
    )

    if has_targets:
        with pytest.raises(RuntimeError, match="No enabled PackerBuildTarget"):
            jobs.select_build_node(template, fail_if_targets_exhausted=True)
    else:
        assert jobs.select_build_node(template, fail_if_targets_exhausted=True) == (
            "https://primary.example:8006",
            "pve-primary",
        )


def test_cloud_selection_never_falls_back_when_enabled_targets_are_at_capacity(
    isolated_imports,
) -> None:
    jobs = _import_jobs_module()
    target = SimpleNamespace(
        enabled=True,
        priority=1,
        proxmox_endpoint="https://target.example:8006",
        proxmox_node="pve-target",
    )
    targets = Mock()
    targets.all.return_value.order_by.return_value = [target]
    build_filter = Mock()
    build_filter.count.return_value = 2
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerBuild = type(
        "PackerBuild",
        (),
        {"objects": SimpleNamespace(filter=Mock(return_value=build_filter))},
    )
    sys.modules["netbox_packer.models"] = models_mod
    validators_mod = types.ModuleType("netbox_packer.validators")
    validators_mod.NodeAffinityValidator = object
    sys.modules["netbox_packer.validators"] = validators_mod
    template = SimpleNamespace(
        name="cloud-template",
        proxmox_endpoint="https://primary.example:8006",
        proxmox_node="pve-primary",
        build_targets=targets,
    )

    with pytest.raises(RuntimeError, match="No authorized build target"):
        jobs.select_build_node(
            template,
            skip_affinity_check=True,
            fail_if_targets_exhausted=True,
        )


def test_cloud_selection_never_falls_back_when_affinity_rejects_every_target(
    isolated_imports,
) -> None:
    jobs = _import_jobs_module()
    target = SimpleNamespace(
        enabled=True,
        priority=1,
        proxmox_endpoint="https://target.example:8006",
        proxmox_node="pve-target",
    )
    targets = Mock()
    targets.all.return_value.order_by.return_value = [target]
    build_filter = Mock()
    build_filter.count.return_value = 0
    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerBuild = type(
        "PackerBuild",
        (),
        {"objects": SimpleNamespace(filter=Mock(return_value=build_filter))},
    )
    sys.modules["netbox_packer.models"] = models_mod

    class RejectAffinity:
        def __init__(self, template):
            self.template = template

        def validate(self):
            return False, ["node rejected"], []

    validators_mod = types.ModuleType("netbox_packer.validators")
    validators_mod.NodeAffinityValidator = RejectAffinity
    sys.modules["netbox_packer.validators"] = validators_mod
    template = SimpleNamespace(
        name="cloud-template",
        proxmox_endpoint="https://primary.example:8006",
        proxmox_node="pve-primary",
        build_targets=targets,
    )

    with pytest.raises(RuntimeError, match="No authorized build target"):
        jobs.select_build_node(template, fail_if_targets_exhausted=True)

    assert template.proxmox_endpoint == "https://primary.example:8006"
    assert template.proxmox_node == "pve-primary"


def test_dispatch_build_marks_build_failed_when_enqueue_raises(isolated_imports) -> None:
    jobs = _import_jobs_module()
    jobs.PackerBuildJob.enqueue = Mock(side_effect=RuntimeError("queue offline"))

    build_filter = Mock()
    build_filter.exclude.return_value.exists.return_value = False
    template_filter = Mock()

    class PackerBuild:
        objects = SimpleNamespace(filter=Mock(return_value=build_filter))

    class PackerTemplate:
        objects = SimpleNamespace(filter=Mock(return_value=template_filter))

    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerBuild = PackerBuild
    models_mod.PackerTemplate = PackerTemplate
    sys.modules["netbox_packer.models"] = models_mod

    build = SimpleNamespace(pk=321, template_id=654, status="queued", log="", save=Mock())

    with pytest.raises(RuntimeError, match="queue offline"):
        jobs.dispatch_build(build)

    assert build.status == "failed"
    assert "[ERROR] Failed to enqueue PackerBuildJob: queue offline" in build.log
    build.save.assert_called_once_with(update_fields=["status", "finished_at", "log"])
    PackerBuild.objects.filter.assert_called_once_with(
        template_id=654,
        status__in=("queued", "running"),
    )
    build_filter.exclude.assert_called_once_with(pk=321)
    PackerTemplate.objects.filter.assert_called_once_with(pk=654)
    template_filter.update.assert_called_once_with(build_status="failed")


def test_dispatch_authorization_failure_never_enqueues_and_marks_failed(
    isolated_imports,
) -> None:
    jobs = _import_jobs_module()
    jobs._authorize_cloud_build_dispatch = Mock(side_effect=RuntimeError("endpoint authorization revoked"))
    jobs.PackerBuildJob.enqueue = Mock()

    build_filter = Mock()
    build_filter.exclude.return_value.exists.return_value = False
    template_filter = Mock()

    class PackerBuild:
        objects = SimpleNamespace(filter=Mock(return_value=build_filter))

    class PackerTemplate:
        objects = SimpleNamespace(filter=Mock(return_value=template_filter))

    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerBuild = PackerBuild
    models_mod.PackerTemplate = PackerTemplate
    sys.modules["netbox_packer.models"] = models_mod
    build = SimpleNamespace(pk=322, template_id=655, status="queued", log="", save=Mock())

    with pytest.raises(RuntimeError, match="authorization revoked"):
        jobs.dispatch_build(build)

    jobs.PackerBuildJob.enqueue.assert_not_called()
    assert build.status == "failed"
    assert "endpoint authorization revoked" in build.log
    template_filter.update.assert_called_once_with(build_status="failed")


def test_run_subprocess_timeout_kills_silent_process(isolated_imports) -> None:
    jobs = _import_jobs_module()
    build = SimpleNamespace(log="", save=Mock())
    log_lines: list[str] = []

    started = time.monotonic()
    exit_code = jobs.PackerBuildJob()._run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        build,
        log_lines,
        timeout=1,
        phase="silent-test",
    )
    elapsed = time.monotonic() - started

    assert exit_code == 124
    assert elapsed < 4
    assert "[ERROR] Timeout exceeded (1s) during silent-test" in log_lines
    build.save.assert_called_with(update_fields=["log"])


def test_caller_endpoint_id_cannot_override_proxbox_api_ssh_authority(
    isolated_imports,
) -> None:
    jobs = _import_jobs_module()
    template = SimpleNamespace(
        proxmox_endpoint="https://legacy-pve.example:8006",
        proxmox_node="legacy-node",
        proxmox_template_id=9050,
        storage_pool="local",
    )
    overrides = {
        "endpoint_id": 11,
        "target_node": "pve-selected",
        "ssh_host": "must-not-bypass-endpoint.example",
    }

    assert jobs._resolve_target_node(template, "affinity-node", overrides) == "pve-selected"
    assert jobs._resolve_ssh_host(template, overrides) is None
    assert jobs._resolve_template_vmid(template, {"template_vmid": 19050}) == 19050
    assert jobs._resolve_storage(template, {"storage": "fast-zfs"}) == "fast-zfs"


def test_cloud_build_never_sends_legacy_url_derived_ssh_authority(isolated_imports) -> None:
    jobs = _import_jobs_module()
    template = SimpleNamespace(
        proxmox_endpoint="https://legacy-pve.example:8006",
        proxmox_node="legacy-node",
        proxmox_template_id=9050,
        storage_pool="local",
    )

    assert jobs._resolve_target_node(template, None, {}) == "legacy-node"
    assert jobs._resolve_ssh_host(template, {}) is None
    assert jobs._resolve_template_vmid(template, {}) == 9050
    assert jobs._resolve_storage(template, {}) == "local"


@pytest.mark.parametrize(
    "overrides",
    (
        {"nested": {"api-token": "do-not-persist"}},
        {"content": '{"token":"do-not-persist"}'},
        {"content": "password: do-not-persist"},
        {"content": "Authorization: Bearer do-not-persist"},
        {"content": "https://operator:do-not-persist@example.invalid/config"},
        {"content": "-----BEGIN OPENSSH PRIVATE KEY-----"},
        {"content": "safe\x00unsafe"},
        {"image_url": "https://images.example/base.qcow2?token=do-not-persist"},
        {
            "image_url": (
                "https://images.example/base.qcow2?X-Amz-Credential=do-not-persist&X-Amz-Signature=do-not-persist"
            )
        },
    ),
)
def test_build_overrides_reject_nested_or_embedded_secrets(
    isolated_imports,
    overrides,
) -> None:
    serializers = _import_api_serializers_module()
    assert serializers._contains_secret_material(overrides) is True


def test_build_overrides_allow_typed_non_secret_selectors(isolated_imports) -> None:
    serializers = _import_api_serializers_module()
    assert (
        serializers._contains_secret_material(
            {
                "endpoint_id": 11,
                "target_node": "pve-selected",
                "template_vmid": 19050,
                "storage": "fast-zfs",
                "image_url": "https://cloud-images.ubuntu.com/image.qcow2",
            }
        )
        is False
    )


@pytest.mark.parametrize(
    "image_url",
    (
        "https://images.example/base.qcow2?token=do-not-persist",
        ("https://images.example/base.qcow2?X-Amz-Credential=do-not-persist&X-Amz-Signature=do-not-persist"),
    ),
)
def test_generic_api_serializers_reject_credentialed_base_image_urls(
    isolated_imports,
    image_url,
) -> None:
    serializers = _import_api_serializers_module()

    with pytest.raises(ValueError, match="forbidden"):
        serializers.PackerBuildSerializer().validate_variable_overrides({"image_url": image_url})
    with pytest.raises(ValueError, match="must not contain"):
        serializers.PackerTemplateSerializer().validate_base_image_url(image_url)


@pytest.mark.parametrize(
    "image_url",
    (
        "https://images.example/base.qcow2?token=do-not-persist",
        ("https://images.example/base.qcow2?X-Amz-Credential=do-not-persist&X-Amz-Signature=do-not-persist"),
    ),
)
def test_models_reject_credentialed_base_image_urls(
    isolated_imports,
    image_url,
) -> None:
    models = _import_models_module()

    template = models.PackerTemplate(
        base_image_url=image_url,
        base_image_sha256="a" * 64,
        installer_config=SimpleNamespace(installer_type="cloud_config"),
    )
    with pytest.raises(models.ValidationError, match="must not contain"):
        template.clean()

    build = models.PackerBuild(variable_overrides={"image_url": image_url})
    with pytest.raises(models.ValidationError, match="must not contain"):
        build.clean()


def test_template_serializer_rejects_plaintext_nms_agent_backend(isolated_imports) -> None:
    serializers = _import_api_serializers_module()
    serializer = serializers.PackerTemplateSerializer()

    with pytest.raises(ValueError, match="HTTPS URL"):
        serializer.validate_nms_agent_backend_url("http://backend.nms.nmulti.cloud")

    assert (
        serializer.validate_nms_agent_backend_url("https://backend.nms.nmulti.cloud")
        == "https://backend.nms.nmulti.cloud"
    )


class ChainManager:
    def __init__(self):
        self.create = Mock()
        self.filter = Mock(return_value=self)
        self.update = Mock()

    def all(self):
        return self

    def select_related(self, *args):
        return self

    def prefetch_related(self, *args):
        return self


def _install_view_model_stubs(template, build_manager, template_manager) -> None:
    models_mod = types.ModuleType("netbox_packer.models")
    for name in ("PackerInstallerConfig", "PackerBuildTarget"):
        model = type(name, (), {"objects": ChainManager()})
        setattr(models_mod, name, model)

    models_mod.PackerTemplate = type("PackerTemplate", (), {"objects": template_manager})
    models_mod.PackerBuild = type("PackerBuild", (), {"objects": build_manager})
    sys.modules["netbox_packer.models"] = models_mod

    def get_object_or_404(*args, **kwargs):
        return template

    shortcuts = types.ModuleType("django.shortcuts")
    shortcuts.get_object_or_404 = get_object_or_404
    shortcuts.redirect = Mock(side_effect=lambda target: {"redirect": target})
    sys.modules["django.shortcuts"] = shortcuts


def _install_django_view_stubs() -> types.ModuleType:
    messages = types.ModuleType("django.contrib.messages")
    messages.success = Mock()
    messages.error = Mock()

    django_contrib = types.ModuleType("django.contrib")
    django_contrib.messages = messages

    exceptions = types.ModuleType("django.core.exceptions")
    exceptions.PermissionDenied = type("PermissionDenied", (Exception,), {})

    generic = types.ModuleType("netbox.views.generic")
    for name in ("ObjectView", "ObjectListView", "ObjectEditView", "ObjectDeleteView"):
        setattr(generic, name, type(name, (), {}))

    netbox_views = types.ModuleType("netbox.views")
    netbox_views.generic = generic

    utilities_views = types.ModuleType("utilities.views")

    def register_model_view(*_args, **_kwargs):
        return lambda cls: cls

    utilities_views.register_model_view = Mock(side_effect=register_model_view)

    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.contrib"] = django_contrib
    sys.modules["django.contrib.messages"] = messages
    sys.modules["django.core"] = types.ModuleType("django.core")
    sys.modules["django.core.exceptions"] = exceptions
    sys.modules["netbox"] = types.ModuleType("netbox")
    sys.modules["netbox.views"] = netbox_views
    sys.modules["netbox.views.generic"] = generic
    sys.modules["utilities"] = types.ModuleType("utilities")
    sys.modules["utilities.views"] = utilities_views
    return messages


def _install_project_view_stubs(dispatch_build) -> None:
    jobs_mod = types.ModuleType("netbox_packer.jobs")
    jobs_mod.dispatch_build = dispatch_build
    sys.modules["netbox_packer.jobs"] = jobs_mod

    for rel in ("filtersets", "forms", "tables"):
        module = types.ModuleType(f"netbox_packer.{rel}")
        for attr in (
            "PackerInstallerConfigFilterSet",
            "PackerTemplateFilterSet",
            "PackerBuildFilterSet",
            "PackerBuildTargetFilterSet",
            "PackerInstallerConfigFilterForm",
            "PackerTemplateFilterForm",
            "PackerBuildFilterForm",
            "PackerBuildTargetFilterForm",
            "PackerInstallerConfigForm",
            "PackerTemplateForm",
            "PackerTemplateCreateInstanceForm",
            "PackerBuildForm",
            "PackerBuildTargetForm",
            "PackerInstallerConfigTable",
            "PackerTemplateTable",
            "PackerBuildTable",
            "PackerBuildTargetTable",
        ):
            setattr(module, attr, object)
        sys.modules[f"netbox_packer.{rel}"] = module


def test_ui_build_view_creates_build_and_dispatches_it(isolated_imports) -> None:
    _install_package()
    messages = _install_django_view_stubs()

    template = SimpleNamespace(pk=12, name="ubuntu-template")
    build = SimpleNamespace(pk=77, status="queued", get_absolute_url=Mock(return_value="/builds/77/"))
    build_manager = ChainManager()
    build_manager.create.return_value = build
    template_manager = ChainManager()
    dispatch_build = Mock()

    _install_view_model_stubs(template, build_manager, template_manager)
    _install_project_view_stubs(dispatch_build)
    views = importlib.import_module("netbox_packer.views")

    response = views.PackerTemplateBuildView().post(SimpleNamespace(user="alice"), pk=12)

    build_manager.create.assert_called_once_with(
        template=template,
        triggered_by="alice",
        status="queued",
    )
    template_manager.filter.assert_called_once_with(pk=12)
    template_manager.update.assert_called_once_with(build_status="building")
    dispatch_build.assert_called_once_with(build)
    messages.success.assert_called_once()
    assert response == {"redirect": "/builds/77/"}


def _install_api_import_stubs(template, build_manager, template_manager, dispatch_build) -> None:
    _install_package()
    api_package = types.ModuleType("netbox_packer.api")
    api_package.__path__ = [str(PKG / "api")]
    sys.modules["netbox_packer.api"] = api_package

    _install_view_model_stubs(template, build_manager, template_manager)
    _install_project_view_stubs(dispatch_build)

    status_mod = types.ModuleType("rest_framework.status")
    status_mod.HTTP_200_OK = 200
    status_mod.HTTP_202_ACCEPTED = 202
    status_mod.HTTP_400_BAD_REQUEST = 400
    status_mod.HTTP_409_CONFLICT = 409
    status_mod.HTTP_503_SERVICE_UNAVAILABLE = 503

    decorators = types.ModuleType("rest_framework.decorators")

    def action(*_args, **_kwargs):
        return lambda method: method

    decorators.action = Mock(side_effect=action)

    response_mod = types.ModuleType("rest_framework.response")

    class Response:
        def __init__(self, data, status=None):
            self.data = data
            self.status_code = status

    response_mod.Response = Response

    exceptions_mod = types.ModuleType("rest_framework.exceptions")
    exceptions_mod.PermissionDenied = type("PermissionDenied", (Exception,), {})

    rest_views = types.ModuleType("rest_framework.views")
    rest_views.APIView = type("APIView", (), {})

    viewsets = types.ModuleType("netbox.api.viewsets")

    class NetBoxModelViewSet:
        def get_object(self):
            return self.template

    viewsets.NetBoxModelViewSet = NetBoxModelViewSet

    serializers = types.ModuleType("netbox_packer.api.serializers")

    class PackerBuildSerializer:
        def __init__(self, instance, context=None, many=False):
            self.instance = instance
            self.data = {"id": instance.pk, "status": instance.status}

    class PackerTemplateBuildRequestSerializer:
        def __init__(self, data):
            self.validated_data = {
                "skip_node_validation": bool(data.get("skip_node_validation", False)),
                "variable_overrides": data.get("variable_overrides", {}),
            }

        def is_valid(self, raise_exception=False):
            return True

    for name in (
        "PackerBuildTargetSerializer",
        "PackerInstallerConfigSerializer",
        "PackerTemplateSerializer",
    ):
        setattr(serializers, name, object)
    serializers.PackerBuildSerializer = PackerBuildSerializer
    serializers.PackerTemplateBuildRequestSerializer = PackerTemplateBuildRequestSerializer

    validators = types.ModuleType("netbox_packer.validators")

    class NodeAffinityValidator:
        def __init__(self, template):
            self.template = template

        def validate(self):
            return True, [], []

    validators.NodeAffinityValidator = NodeAffinityValidator

    sys.modules["rest_framework"] = types.ModuleType("rest_framework")
    sys.modules["rest_framework.status"] = status_mod
    sys.modules["rest_framework.decorators"] = decorators
    sys.modules["rest_framework.response"] = response_mod
    sys.modules["rest_framework.exceptions"] = exceptions_mod
    sys.modules["rest_framework.views"] = rest_views
    sys.modules["netbox"] = types.ModuleType("netbox")
    sys.modules["netbox.api"] = types.ModuleType("netbox.api")
    sys.modules["netbox.api.viewsets"] = viewsets
    sys.modules["netbox_packer.api.serializers"] = serializers
    sys.modules["netbox_packer.validators"] = validators


def test_api_build_action_creates_build_and_dispatches_it(isolated_imports) -> None:
    template = SimpleNamespace(
        pk=12,
        name="ubuntu-template",
        proxmox_endpoint="https://pve.example:8006",
        proxmox_node="pve01",
    )
    build = SimpleNamespace(pk=88, status="queued")
    build_manager = ChainManager()
    build_manager.create.return_value = build
    template_manager = ChainManager()
    dispatch_build = Mock()
    _install_api_import_stubs(template, build_manager, template_manager, dispatch_build)
    api_views = importlib.import_module("netbox_packer.api.views")

    view = api_views.PackerTemplateViewSet()
    view.template = template
    request = SimpleNamespace(
        user="api-user",
        data={"variable_overrides": {"image_url": "https://example.invalid/base.qcow2"}},
    )

    response = view.build(request, pk=12)

    build_manager.create.assert_called_once_with(
        template=template,
        triggered_by="api-user",
        variable_overrides={"image_url": "https://example.invalid/base.qcow2"},
        status="queued",
    )
    template_manager.filter.assert_called_once_with(pk=12)
    template_manager.update.assert_called_once_with(build_status="building")
    dispatch_build.assert_called_once_with(build)
    assert response.status_code == 202
    assert response.data == {"id": 88, "status": "queued"}


def test_api_endpoint_agnostic_cloud_build_uses_target_without_numeric_endpoint_id(
    isolated_imports,
) -> None:
    template = SimpleNamespace(
        pk=12,
        name="endpoint-agnostic-cloud-template",
        proxmox_endpoint="",
        proxmox_node="select-at-build",
        installer_config=SimpleNamespace(installer_type="cloud_config"),
    )
    build = SimpleNamespace(pk=89, status="queued")
    build_manager = ChainManager()
    build_manager.create.return_value = build
    template_manager = ChainManager()
    dispatch_build = Mock()
    _install_api_import_stubs(template, build_manager, template_manager, dispatch_build)
    api_views = importlib.import_module("netbox_packer.api.views")

    view = api_views.PackerTemplateViewSet()
    view.template = template
    request = SimpleNamespace(
        user="api-user",
        data={"variable_overrides": {"target_node": "pve01"}},
    )

    response = view.build(request, pk=12)

    build_manager.create.assert_called_once_with(
        template=template,
        triggered_by="api-user",
        variable_overrides={"target_node": "pve01"},
        status="queued",
    )
    dispatch_build.assert_called_once_with(build)
    assert response.status_code == 202


def test_api_endpoint_agnostic_cloud_build_requires_target_node(
    isolated_imports,
) -> None:
    template = SimpleNamespace(
        pk=12,
        name="endpoint-agnostic-cloud-template",
        proxmox_endpoint="",
        proxmox_node="select-at-build",
        installer_config=SimpleNamespace(installer_type="cloud_config"),
    )
    build_manager = ChainManager()
    template_manager = ChainManager()
    dispatch_build = Mock()
    _install_api_import_stubs(template, build_manager, template_manager, dispatch_build)
    api_views = importlib.import_module("netbox_packer.api.views")

    view = api_views.PackerTemplateViewSet()
    view.template = template
    request = SimpleNamespace(user="api-user", data={"variable_overrides": {}})

    response = view.build(request, pk=12)

    build_manager.create.assert_not_called()
    dispatch_build.assert_not_called()
    assert response.status_code == 400
    assert "PackerBuildTarget URL supplies endpoint identity" in response.data["detail"]


def test_influxdb_profile_readiness_rejects_a_stale_ready_template(isolated_imports) -> None:
    templates = [
        SimpleNamespace(
            pk=50,
            name="influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics",
            build_status="ready",
            is_stale=True,
        ),
        SimpleNamespace(
            pk=51,
            name="influxdb-core-3.11.0-ubuntu-2404",
            build_status="ready",
            is_stale=False,
        ),
    ]
    build_manager = ChainManager()
    template_manager = ChainManager()
    template_manager.filter.return_value = templates
    _install_api_import_stubs(templates[0], build_manager, template_manager, Mock())
    api_views = importlib.import_module("netbox_packer.api.views")

    request = SimpleNamespace(user=SimpleNamespace(has_perm=Mock(return_value=True)))
    response = api_views.InfluxDBProfileListView().get(request)
    profiles = {profile["template_id"]: profile for profile in response.data["profiles"]}

    assert profiles[50]["build_status"] == "ready"
    assert profiles[50]["ready"] is False
    assert profiles[51]["ready"] is True


class StalenessTemplateManager:
    def __init__(self, templates):
        self.templates = templates
        self.exclude = Mock(side_effect=lambda **_kwargs: self.templates)
        self.update_calls = []

    def filter(self, *, pk):
        template = next(item for item in self.templates if item.pk == pk)
        manager = self

        class Query:
            def update(self, **values):
                manager.update_calls.append((pk, values))
                for key, value in values.items():
                    setattr(template, key, value)
                return 1

        return Query()


class StalenessBuildManager:
    def __init__(self, template, queued_build=None):
        self.template = template
        self.builds = {} if queued_build is None else {queued_build.pk: queued_build}
        self.create = Mock(side_effect=self._create)

    def _create(self, **values):
        build = SimpleNamespace(
            pk=70,
            template_id=values["template"].pk,
            **values,
        )
        self.builds[build.pk] = build
        return build

    def filter(self, *, template, status):
        matches = [
            build for build in self.builds.values() if build.template_id == template.pk and build.status == status
        ]

        class Query:
            def exists(self):
                return bool(matches)

            def order_by(self, *_fields):
                return self

            def first(self):
                return matches[0] if matches else None

        return Query()

    def select_related(self, *_fields):
        return self

    def get(self, *, pk):
        return self.builds[pk]


@pytest.mark.parametrize("recover_wedged_build", [False, True])
@pytest.mark.parametrize("branching_enabled", [False, True])
def test_staleness_job_dispatches_pin_only_drift_once(
    isolated_imports,
    recover_wedged_build,
    branching_enabled,
) -> None:
    jobs = _import_jobs_module()
    events = []
    enqueue = Mock(side_effect=lambda **_kwargs: events.append("enqueue"))
    jobs.PackerBuildJob.enqueue = enqueue

    template = SimpleNamespace(
        pk=60,
        name="pin-drifted-template",
        build_status="ready",
        is_stale=True,
        max_age_days=None,
        auto_rebuild=True,
    )
    wedged_build = None
    if recover_wedged_build:
        wedged_build = SimpleNamespace(
            pk=71,
            template_id=template.pk,
            template=template,
            status="queued",
        )
    template_manager = StalenessTemplateManager([template])
    build_manager = StalenessBuildManager(template, wedged_build)

    models_mod = types.ModuleType("netbox_packer.models")
    models_mod.PackerTemplate = type("PackerTemplate", (), {"objects": template_manager})
    models_mod.PackerBuild = type("PackerBuild", (), {"objects": build_manager})
    sys.modules["netbox_packer.models"] = models_mod

    branch_lifecycle = types.ModuleType("netbox_packer.services.branch_lifecycle")
    branch = SimpleNamespace(name="test-branch")
    branch_config = {"prefix": "stale", "on_conflict": "fail"} if branching_enabled else None
    branch_lifecycle.branching_enabled_settings = Mock(return_value=branch_config)
    branch_lifecycle.create_and_provision_branch = Mock(return_value=branch)

    class BranchContext:
        def __enter__(self):
            events.append("branch-enter")

        def __exit__(self, *_args):
            events.append("branch-exit")

    branch_lifecycle.activate_branch_context = Mock(return_value=BranchContext())

    def merge_branch(**_kwargs):
        events.append("merge")
        return True, "merged"

    branch_lifecycle.merge_branch = Mock(side_effect=merge_branch)
    sys.modules["netbox_packer.services.branch_lifecycle"] = branch_lifecycle

    jobs.PackerStalenessCheckJob().run()

    expected_build_id = 71 if recover_wedged_build else 70
    enqueue.assert_called_once_with(build_id=expected_build_id)
    assert template.build_status == "building"
    if branching_enabled:
        assert events.index("branch-exit") < events.index("merge") < events.index("enqueue")
    else:
        branch_lifecycle.merge_branch.assert_not_called()
    if recover_wedged_build:
        build_manager.create.assert_not_called()
    else:
        build_manager.create.assert_called_once_with(
            template=template,
            triggered_by="PackerStalenessCheckJob",
            status="queued",
        )


@pytest.mark.parametrize("recover_wedged_build", [False, True])
def test_staleness_management_command_dispatches_pin_only_drift(
    isolated_imports,
    recover_wedged_build,
) -> None:
    _install_package()

    class Output:
        def __init__(self):
            self.lines = []

        def write(self, value):
            self.lines.append(value)

    class Style:
        def __getattr__(self, _name):
            return lambda value: value

    class BaseCommand:
        def __init__(self):
            self.stdout = Output()
            self.style = Style()

    base_module = types.ModuleType("django.core.management.base")
    base_module.BaseCommand = BaseCommand
    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.core"] = types.ModuleType("django.core")
    sys.modules["django.core.management"] = types.ModuleType("django.core.management")
    sys.modules["django.core.management.base"] = base_module

    template = SimpleNamespace(
        pk=80,
        name="pin-only-drift",
        build_status="ready",
        is_stale=True,
        age_days=0,
        max_age_days=None,
        auto_rebuild=True,
    )
    template_manager = StalenessTemplateManager([template])
    wedged_build = None
    if recover_wedged_build:
        wedged_build = SimpleNamespace(
            pk=81,
            template_id=template.pk,
            template=template,
            status="queued",
        )
    build_manager = StalenessBuildManager(template, wedged_build)
    models_module = types.ModuleType("netbox_packer.models")
    models_module.PackerTemplate = type("PackerTemplate", (), {"objects": template_manager})
    models_module.PackerBuild = type("PackerBuild", (), {"objects": build_manager})
    sys.modules["netbox_packer.models"] = models_module

    dispatch = Mock()
    jobs_module = types.ModuleType("netbox_packer.jobs")
    jobs_module.dispatch_build = dispatch
    sys.modules["netbox_packer.jobs"] = jobs_module

    command_module = importlib.import_module("netbox_packer.management.commands.check_packer_staleness")
    command_module.Command().handle(dry_run=False)

    template_manager.exclude.assert_called_once_with(build_status__in=("building",))
    expected_build_id = 81 if recover_wedged_build else 70
    dispatch.assert_called_once_with(build_manager.builds[expected_build_id])
    assert template.build_status == "building"
    if recover_wedged_build:
        build_manager.create.assert_not_called()


def _load_migration_0029():
    django_db = types.ModuleType("django.db")

    class Migration:
        pass

    class RunPython:
        def __init__(self, forwards, backwards):
            self.forwards = forwards
            self.backwards = backwards

    django_db.migrations = SimpleNamespace(Migration=Migration, RunPython=RunPython)
    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.db"] = django_db

    path = PKG / "migrations" / "0029_pin_influxdb3_debian13_base_image.py"
    spec = importlib.util.spec_from_file_location("migration_0029_behavior", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationTemplateManager:
    def __init__(self, rows, forced_update_count=None):
        self.rows = rows
        self.forced_update_count = forced_update_count

    def select_for_update(self):
        return MigrationTemplateQuery(self)

    def filter(self, **criteria):
        return MigrationTemplateQuery(self, criteria)


class MigrationTemplateQuery:
    def __init__(self, manager, criteria=None):
        self.manager = manager
        self.criteria = criteria or {}

    def filter(self, **criteria):
        return MigrationTemplateQuery(self.manager, {**self.criteria, **criteria})

    def _matches(self):
        return [
            row for row in self.manager.rows if all(getattr(row, key) == value for key, value in self.criteria.items())
        ]

    def first(self):
        matches = self._matches()
        return matches[0] if matches else None

    def update(self, **values):
        if self.manager.forced_update_count is not None:
            return self.manager.forced_update_count
        matches = self._matches()
        for row in matches:
            for key, value in values.items():
                setattr(row, key, value)
        return len(matches)


def _migration_apps(manager):
    model = type("PackerTemplate", (), {"objects": manager})
    return SimpleNamespace(get_model=Mock(return_value=model))


@pytest.mark.parametrize("renamed", [False, True])
def test_migration_0029_fails_closed_when_expected_template_is_missing(
    isolated_imports,
    renamed,
) -> None:
    migration = _load_migration_0029()
    rows = []
    if renamed:
        rows.append(
            SimpleNamespace(
                pk=1,
                name="operator-renamed-template",
                base_image_url="",
                base_image_sha256="",
            )
        )

    with pytest.raises(RuntimeError, match="zero profiles are pinned"):
        migration.pin_influxdb3_debian13_base_image(
            _migration_apps(MigrationTemplateManager(rows)),
            None,
        )

    if rows:
        assert rows[0].base_image_url == ""
        assert rows[0].base_image_sha256 == ""


def test_migration_0029_persists_one_pin_and_is_idempotent(isolated_imports) -> None:
    migration = _load_migration_0029()
    row = SimpleNamespace(
        pk=1,
        name=migration.TEMPLATE_NAME,
        base_image_url="",
        base_image_sha256="",
    )
    apps = _migration_apps(MigrationTemplateManager([row]))

    migration.pin_influxdb3_debian13_base_image(apps, None)
    assert row.base_image_url == migration.PINNED_IMAGE_URL
    assert row.base_image_sha256 == migration.PINNED_IMAGE_SHA256

    migration.pin_influxdb3_debian13_base_image(apps, None)
    assert row.base_image_url == migration.PINNED_IMAGE_URL
    assert row.base_image_sha256 == migration.PINNED_IMAGE_SHA256


def test_migration_0029_refuses_a_different_operator_pin(isolated_imports) -> None:
    migration = _load_migration_0029()
    row = SimpleNamespace(
        pk=1,
        name=migration.TEMPLATE_NAME,
        base_image_url="https://operator.example/base.qcow2",
        base_image_sha256="operator-reviewed-digest",
    )

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        migration.pin_influxdb3_debian13_base_image(
            _migration_apps(MigrationTemplateManager([row])),
            None,
        )

    assert row.base_image_url == "https://operator.example/base.qcow2"
    assert row.base_image_sha256 == "operator-reviewed-digest"


def test_migration_0029_rejects_a_zero_row_compare_and_set(isolated_imports) -> None:
    migration = _load_migration_0029()
    row = SimpleNamespace(
        pk=1,
        name=migration.TEMPLATE_NAME,
        base_image_url="",
        base_image_sha256="",
    )
    manager = MigrationTemplateManager([row], forced_update_count=0)

    with pytest.raises(RuntimeError, match="expected to update exactly one"):
        migration.pin_influxdb3_debian13_base_image(
            _migration_apps(manager),
            None,
        )

    assert row.base_image_url == ""
    assert row.base_image_sha256 == ""


def _load_migration_0030():
    django_db = types.ModuleType("django.db")

    class Migration:
        pass

    class RunPython:
        def __init__(self, forwards, backwards):
            self.forwards = forwards
            self.backwards = backwards

    django_db.migrations = SimpleNamespace(Migration=Migration, RunPython=RunPython)
    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.db"] = django_db

    path = PKG / "migrations" / "0030_seed_influxdb3_explorer_debian13_cloud_init.py"
    spec = importlib.util.spec_from_file_location("migration_0030_behavior", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SeedRowManager:
    def __init__(self, rows, first_pk):
        self.rows = rows
        self.first_pk = first_pk

    def get_or_create(self, defaults, **identity):
        for row in self.rows:
            if all(getattr(row, field) == expected for field, expected in identity.items()):
                return row, False

        row_values = {**identity, **defaults}
        row = SimpleNamespace(pk=self.first_pk + len(self.rows), **row_values)
        if "installer_config" in defaults:
            row.installer_config_id = defaults["installer_config"].pk
        self.rows.append(row)
        return row, True


def _migration_0030_apps(config_manager, template_manager):
    models = {
        "PackerInstallerConfig": type(
            "PackerInstallerConfig",
            (),
            {"objects": config_manager},
        ),
        "PackerTemplate": type(
            "PackerTemplate",
            (),
            {"objects": template_manager},
        ),
    }
    return SimpleNamespace(get_model=Mock(side_effect=lambda _app, model_name: models[model_name]))


def test_migration_0030_seeds_and_reapplies_after_build_state_changes(
    isolated_imports,
) -> None:
    migration = _load_migration_0030()
    configs = []
    templates = []
    apps = _migration_0030_apps(
        SeedRowManager(configs, first_pk=10),
        SeedRowManager(templates, first_pk=20),
    )

    migration.seed_influxdb3_explorer_debian13(apps, None)

    assert len(configs) == 1
    assert configs[0].name == migration.CONFIG_NAME
    assert configs[0].version == migration.CONFIG_VERSION
    assert configs[0].content == migration.INFLUXDB3_EXPLORER_DEBIAN13_CLOUD_CONFIG
    assert len(templates) == 1
    assert templates[0].name == migration.TEMPLATE_NAME
    assert templates[0].proxmox_template_id == 9053
    assert templates[0].installer_config_id == configs[0].pk

    # These fields are written by a successful build and must not turn a safe
    # rollback/reapply into a false collision.
    templates[0].build_status = "ready"
    templates[0].packer_template_ref = "artifact/explorer/9053"
    migration.seed_influxdb3_explorer_debian13(apps, None)
    assert templates[0].build_status == "ready"
    assert templates[0].packer_template_ref == "artifact/explorer/9053"


def test_migration_0030_refuses_installer_config_collision_without_overwrite(
    isolated_imports,
) -> None:
    migration = _load_migration_0030()
    configs = []
    templates = []
    apps = _migration_0030_apps(
        SeedRowManager(configs, first_pk=10),
        SeedRowManager(templates, first_pk=20),
    )
    migration.seed_influxdb3_explorer_debian13(apps, None)
    configs[0].description = "operator-owned description"

    with pytest.raises(RuntimeError, match="installer config.*description"):
        migration.seed_influxdb3_explorer_debian13(apps, None)

    assert configs[0].description == "operator-owned description"
    assert len(configs) == 1
    assert len(templates) == 1


def test_migration_0030_refuses_template_collision_without_overwrite(
    isolated_imports,
) -> None:
    migration = _load_migration_0030()
    configs = []
    templates = []
    apps = _migration_0030_apps(
        SeedRowManager(configs, first_pk=10),
        SeedRowManager(templates, first_pk=20),
    )
    migration.seed_influxdb3_explorer_debian13(apps, None)
    templates[0].storage_pool = "operator-storage"

    with pytest.raises(RuntimeError, match="template.*storage_pool"):
        migration.seed_influxdb3_explorer_debian13(apps, None)

    assert templates[0].storage_pool == "operator-storage"
    assert len(configs) == 1
    assert len(templates) == 1


def _load_migration_0032():
    django_db = types.ModuleType("django.db")

    class Migration:
        pass

    class RunPython:
        noop = staticmethod(lambda *_args, **_kwargs: None)

        def __init__(self, forwards, backwards):
            self.forwards = forwards
            self.backwards = backwards

    django_db.migrations = SimpleNamespace(Migration=Migration, RunPython=RunPython)
    sys.modules["django"] = types.ModuleType("django")
    sys.modules["django.db"] = django_db

    path = PKG / "migrations" / "0032_update_endpoint_authorization_descriptions.py"
    spec = importlib.util.spec_from_file_location("migration_0032_behavior", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0032_updates_only_exact_historical_descriptions_and_is_idempotent(
    isolated_imports,
) -> None:
    migration = _load_migration_0032()
    rows = [
        SimpleNamespace(name=name, description=old_description)
        for name, old_description, _new_description in migration.DESCRIPTION_UPDATES
    ]
    manager = MigrationTemplateManager(rows)
    apps = _migration_apps(manager)

    migration.update_endpoint_authorization_descriptions(apps, None)

    assert [row.description for row in rows] == [
        new_description for _name, _old_description, new_description in migration.DESCRIPTION_UPDATES
    ]

    migration.update_endpoint_authorization_descriptions(apps, None)
    assert [row.description for row in rows] == [
        new_description for _name, _old_description, new_description in migration.DESCRIPTION_UPDATES
    ]


def test_migration_0032_preserves_operator_edited_and_unrelated_rows(
    isolated_imports,
) -> None:
    migration = _load_migration_0032()
    target_name, _old_description, _new_description = migration.DESCRIPTION_UPDATES[0]
    corrected_name, _corrected_old_description, corrected_description = migration.DESCRIPTION_UPDATES[1]
    rows = [
        SimpleNamespace(name=target_name, description="operator-owned description"),
        SimpleNamespace(name="unrelated-template", description="unrelated description"),
        SimpleNamespace(name=corrected_name, description=corrected_description),
    ]

    migration.update_endpoint_authorization_descriptions(
        _migration_apps(MigrationTemplateManager(rows)),
        None,
    )

    assert [row.description for row in rows] == [
        "operator-owned description",
        "unrelated description",
        corrected_description,
    ]
