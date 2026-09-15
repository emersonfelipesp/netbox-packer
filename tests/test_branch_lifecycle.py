"""Behavior tests for the Packer branch-isolation decision wrapper."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "netbox_packer" / "services" / "branch_lifecycle.py"


def _load_module(monkeypatch: pytest.MonkeyPatch, settings: object):
    """Load the wrapper with a controllable Packer settings model."""

    models = types.ModuleType("netbox_packer.models")

    class PackerPluginSettings:
        @classmethod
        def get_solo(cls) -> object:
            return settings

    models.PackerPluginSettings = PackerPluginSettings
    monkeypatch.setitem(sys.modules, "netbox_packer.models", models)

    module_name = "netbox_packer.services.branch_lifecycle_contract_test"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def _typed_lifecycle(
    state: str,
    *,
    reason: str | None = None,
    available: bool = True,
) -> SimpleNamespace:
    decision = SimpleNamespace(
        state=SimpleNamespace(value=state),
        reason=reason,
    )
    return SimpleNamespace(
        resolve_branching_decision=lambda: decision,
        is_branching_available=lambda: available,
    )


def _enabled_settings() -> SimpleNamespace:
    return SimpleNamespace(
        branching_enabled=True,
        branch_name_prefix="nightly-packer",
        branch_on_conflict="acknowledge",
    )


def test_explicitly_disabled_branching_is_the_only_none_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        monkeypatch,
        SimpleNamespace(branching_enabled=False),
    )
    monkeypatch.setattr(
        module,
        "_proxbox_branch_lifecycle",
        lambda: pytest.fail("disabled branching must not inspect the runtime"),
    )

    assert module.branching_enabled_settings() is None


@pytest.mark.parametrize("invalid_value", [None, 0, "false"])
def test_non_boolean_branching_setting_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    invalid_value: object,
) -> None:
    module = _load_module(
        monkeypatch,
        SimpleNamespace(branching_enabled=invalid_value),
    )

    with pytest.raises(
        module.BranchingUnavailableError,
        match="did not contain a boolean value",
    ):
        module.branching_enabled_settings()


def test_enabled_and_available_branching_returns_packer_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())
    lifecycle = _typed_lifecycle("enabled")
    lifecycle.is_branching_available = lambda: pytest.fail("the typed enabled decision must be preferred")
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: lifecycle)

    assert module.branching_enabled_settings() == {
        "prefix": "nightly-packer",
        "on_conflict": "acknowledge",
    }


def test_enabled_but_unavailable_branching_raises_local_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())
    lifecycle = _typed_lifecycle(
        "configured_but_unavailable",
        reason="the netbox_branching Django app is not loaded",
    )
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: lifecycle)

    with pytest.raises(
        module.BranchingUnavailableError,
        match="netbox_branching Django app is not loaded",
    ):
        module.branching_enabled_settings()

    assert "BranchingUnavailableError" in module.__all__


def test_typed_decision_failure_does_not_fall_back_to_direct_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())

    def fail_decision() -> None:
        raise RuntimeError("decision service failed")

    lifecycle = SimpleNamespace(
        resolve_branching_decision=fail_decision,
        is_branching_available=lambda: pytest.fail("a broken typed resolver must not fall back to the legacy check"),
    )
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: lifecycle)

    with pytest.raises(
        module.BranchingUnavailableError,
        match="decision could not be resolved.*decision service failed",
    ):
        module.branching_enabled_settings()


def test_enabled_branching_fails_closed_when_helpers_cannot_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: None)

    with pytest.raises(module.BranchingUnavailableError, match="requires netbox-proxbox"):
        module.branching_enabled_settings()


def test_unreadable_settings_row_fails_before_runtime_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())

    class UnreadableSettings:
        @classmethod
        def get_solo(cls) -> object:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(module, "PackerPluginSettings", UnreadableSettings)
    monkeypatch.setattr(
        module,
        "_proxbox_branch_lifecycle",
        lambda: pytest.fail("runtime resolution must follow the settings read"),
    )

    with pytest.raises(
        module.BranchingUnavailableError,
        match="branching_enabled could not be read.*database unavailable",
    ):
        module.branching_enabled_settings()


@pytest.mark.parametrize("available", [True, False])
def test_old_netbox_proxbox_fallback_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())
    old_lifecycle = SimpleNamespace(is_branching_available=lambda: available)
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: old_lifecycle)

    if available:
        assert module.branching_enabled_settings() == {
            "prefix": "nightly-packer",
            "on_conflict": "acknowledge",
        }
        return

    with pytest.raises(module.BranchingUnavailableError, match="runtime is unavailable"):
        module.branching_enabled_settings()


def test_typed_disabled_decision_checks_runtime_for_packer_enabled_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())
    lifecycle = _typed_lifecycle("disabled", available=True)
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: lifecycle)

    assert module.branching_enabled_settings() == {
        "prefix": "nightly-packer",
        "on_conflict": "acknowledge",
    }


def test_merge_wrapper_accepts_new_three_value_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(monkeypatch, _enabled_settings())
    lifecycle = SimpleNamespace(merge_branch=lambda **_kwargs: (True, "Branch merged.", None))
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: lifecycle)

    assert module.merge_branch(branch=object(), user=None, on_conflict="fail") == (
        True,
        "Branch merged.",
    )


def test_published_0_0_26_post7_helper_surface_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the published two-value helper contract end to end."""

    module = _load_module(monkeypatch, _enabled_settings())
    merge_calls: list[dict[str, object]] = []

    def _legacy_merge(**kwargs: object) -> tuple[bool, str]:
        merge_calls.append(kwargs)
        return (True, "Branch merged.")

    legacy_lifecycle = SimpleNamespace(
        is_branching_available=lambda: True,
        merge_branch=_legacy_merge,
    )
    assert not hasattr(legacy_lifecycle, "resolve_branching_decision")
    monkeypatch.setattr(module, "_proxbox_branch_lifecycle", lambda: legacy_lifecycle)

    assert module.branching_enabled_settings() == {
        "prefix": "nightly-packer",
        "on_conflict": "acknowledge",
    }
    branch = object()
    assert module.merge_branch(branch=branch, user=None, on_conflict="fail") == (
        True,
        "Branch merged.",
    )
    assert merge_calls == [{"branch": branch, "user": None, "on_conflict": "fail"}]
