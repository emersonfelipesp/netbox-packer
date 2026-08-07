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


def test_dispatch_build_enqueues_with_build_id_keyword(isolated_imports) -> None:
    jobs = _import_jobs_module()
    enqueue = Mock()
    jobs.PackerBuildJob.enqueue = enqueue

    jobs.dispatch_build(SimpleNamespace(pk=123))

    enqueue.assert_called_once_with(build_id=123)
    assert "instance" not in enqueue.call_args.kwargs


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


def test_run_subprocess_never_logs_packer_variable_values(isolated_imports, monkeypatch) -> None:
    jobs = _import_jobs_module()
    canary = "command-line-value-must-not-be-logged"
    process = SimpleNamespace(
        stdout=iter(()),
        poll=Mock(return_value=0),
        wait=Mock(),
        returncode=0,
    )
    monkeypatch.setattr(jobs.subprocess, "Popen", Mock(return_value=process))
    build = SimpleNamespace(log="", save=Mock())
    log_lines: list[str] = []

    result = jobs.PackerBuildJob()._run_subprocess(
        ["packer", "build", f"-var=ordinary_selector={canary}", "template.pkr.hcl"],
        build,
        log_lines,
        timeout=10,
    )

    assert result == 0
    assert canary not in "\n".join(log_lines)
    assert "-var=ordinary_selector=<redacted>" in log_lines[0]


def test_explicit_endpoint_requires_positive_id_and_valid_target(
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
    }

    assert jobs._resolve_target_node(template, "affinity-node", overrides) == "pve-selected"
    assert jobs._resolve_endpoint_id(overrides) == 11
    assert jobs._resolve_template_vmid(template, {"template_vmid": 19050}) == 19050
    assert jobs._resolve_storage(template, {"storage": "fast-zfs"}) == "fast-zfs"


@pytest.mark.parametrize("raw_endpoint", (None, "", 0, -1, True, "invalid"))
def test_invalid_endpoint_ids_fail_closed(isolated_imports, raw_endpoint) -> None:
    jobs = _import_jobs_module()

    assert jobs._resolve_endpoint_id({"endpoint_id": raw_endpoint}) is None


def test_worker_failure_path_recursively_scrubs_nested_image_urls(isolated_imports) -> None:
    jobs = _import_jobs_module()
    canary = "worker-nested-image-url-canary-not-a-secret"
    template_filter = Mock()

    class Template:
        objects = SimpleNamespace(filter=Mock(return_value=template_filter))

    template = Template()
    template.pk = 7
    build = SimpleNamespace(
        variable_overrides={
            "source": {"image_url": f"https://images.example/image.qcow2?token={canary}"},
            "layers": [{"image_url": f"https://images.example/{canary}"}],
            "target_node": "pve01",
        },
        status="queued",
        finished_at=None,
        log="",
        save=Mock(),
    )

    jobs._mark_build_failed_closed(build, template, "Unsafe image source rejected.")

    assert build.variable_overrides == {
        "source": {},
        "layers": [{}],
        "target_node": "pve01",
    }
    assert canary not in repr(build.variable_overrides)
    assert build.status == "failed"
    build.save.assert_called_once_with(update_fields=["variable_overrides", "status", "finished_at", "log"])
    Template.objects.filter.assert_called_once_with(pk=7)
    template_filter.update.assert_called_once_with(build_status="failed")


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
            }
        )
        is False
    )


class ChainManager:
    def __init__(self):
        self.create = Mock()
        self.filter = Mock(return_value=self)
        self.update = Mock()

    def all(self):
        return self

    def restrict(self, *args):
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

    template = SimpleNamespace(pk=12, name="ubuntu-template", installer_config=None)
    build = SimpleNamespace(pk=77, status="queued", get_absolute_url=Mock(return_value="/builds/77/"))
    build_manager = ChainManager()
    build_manager.create.return_value = build
    template_manager = ChainManager()
    dispatch_build = Mock()

    _install_view_model_stubs(template, build_manager, template_manager)
    _install_project_view_stubs(dispatch_build)
    views = importlib.import_module("netbox_packer.views")

    user = Mock()
    user.has_perm.return_value = True
    user.__str__ = Mock(return_value="alice")
    response = views.PackerTemplateBuildView().post(SimpleNamespace(user=user), pk=12)

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


def test_ui_build_view_rejects_view_only_user_before_mutation(isolated_imports) -> None:
    _install_package()
    _install_django_view_stubs()

    template = SimpleNamespace(pk=12, name="ubuntu-template", installer_config=None)
    build_manager = ChainManager()
    template_manager = ChainManager()
    dispatch_build = Mock()
    user = Mock()
    user.has_perm.return_value = False

    _install_view_model_stubs(template, build_manager, template_manager)
    _install_project_view_stubs(dispatch_build)
    views = importlib.import_module("netbox_packer.views")

    with pytest.raises(views.PermissionDenied):
        views.PackerTemplateBuildView().post(SimpleNamespace(user=user), pk=12)

    user.has_perm.assert_called_once_with("netbox_packer.change_packertemplate")
    build_manager.create.assert_not_called()
    template_manager.filter.assert_not_called()
    dispatch_build.assert_not_called()


def test_ui_build_view_rejects_cloud_build_without_creating_doomed_job(isolated_imports) -> None:
    _install_package()
    messages = _install_django_view_stubs()

    installer = SimpleNamespace(installer_type="cloud_config")
    template = SimpleNamespace(
        pk=12,
        name="ubuntu-cloud-template",
        installer_config=installer,
        get_absolute_url=Mock(return_value="/templates/12/"),
    )
    build_manager = ChainManager()
    template_manager = ChainManager()
    dispatch_build = Mock()
    user = Mock()
    user.has_perm.return_value = True

    _install_view_model_stubs(template, build_manager, template_manager)
    _install_project_view_stubs(dispatch_build)
    views = importlib.import_module("netbox_packer.views")

    response = views.PackerTemplateBuildView().post(SimpleNamespace(user=user), pk=12)

    assert response == {"redirect": "/templates/12/"}
    messages.error.assert_called_once()
    build_manager.create.assert_not_called()
    template_manager.filter.assert_not_called()
    dispatch_build.assert_not_called()


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
    authentication = types.ModuleType("netbox.api.authentication")

    class TokenPermissions:
        perms_map = {}

    authentication.TokenPermissions = TokenPermissions

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
    sys.modules["netbox.api.authentication"] = authentication
    sys.modules["netbox.api.viewsets"] = viewsets
    sys.modules["netbox_packer.api.serializers"] = serializers
    sys.modules["netbox_packer.validators"] = validators


def test_api_build_action_creates_build_and_dispatches_it(isolated_imports) -> None:
    template = SimpleNamespace(
        pk=12,
        name="ubuntu-template",
        proxmox_endpoint="https://pve.example:8006",
        proxmox_node="pve01",
        installer_config=None,
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
    user = Mock()
    user.has_perm.return_value = True
    user.__str__ = Mock(return_value="api-user")
    request = SimpleNamespace(
        user=user,
        data={"variable_overrides": {"target_node": "pve01"}},
    )

    response = view.build(request, pk=12)

    build_manager.create.assert_called_once_with(
        template=template,
        triggered_by="api-user",
        variable_overrides={"target_node": "pve01"},
        status="queued",
    )
    template_manager.filter.assert_called_once_with(pk=12)
    template_manager.update.assert_called_once_with(build_status="building")
    dispatch_build.assert_called_once_with(build)
    assert response.status_code == 202
    assert response.data == {"id": 88, "status": "queued"}
    user.has_perm.assert_called_once_with("netbox_packer.change_packertemplate")


def test_api_build_action_rejects_add_only_user_before_mutation(isolated_imports) -> None:
    template = SimpleNamespace(
        pk=12,
        name="ubuntu-template",
        proxmox_endpoint="https://pve.example:8006",
        proxmox_node="pve01",
        installer_config=None,
    )
    build_manager = ChainManager()
    template_manager = ChainManager()
    dispatch_build = Mock()
    _install_api_import_stubs(template, build_manager, template_manager, dispatch_build)
    api_views = importlib.import_module("netbox_packer.api.views")

    user = Mock()
    user.has_perm.return_value = False
    request = SimpleNamespace(user=user, data={"variable_overrides": {}})

    with pytest.raises(api_views.PermissionDenied):
        api_views.PackerTemplateViewSet().build(request, pk=12)

    user.has_perm.assert_called_once_with("netbox_packer.change_packertemplate")
    build_manager.create.assert_not_called()
    template_manager.filter.assert_not_called()
    dispatch_build.assert_not_called()
