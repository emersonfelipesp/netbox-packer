"""Tests for the backward-compatible NetBox support policy."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from packaging.version import InvalidVersion

ROOT = Path(__file__).resolve().parents[1]


def _load_compat_module() -> Any:
    spec = importlib.util.spec_from_file_location("netbox_packer_compat_under_test", ROOT / "netbox_packer/compat.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compat = _load_compat_module()


class _Release:
    def __init__(self, version: str, full_version: str, designation: str | None = None) -> None:
        self.version = version
        self.full_version = full_version
        self.designation = designation


class _AppConfig:
    label = "netbox_packer"
    name = label
    verbose_name = "NetBox Packer"


def _install_fake_django(monkeypatch: pytest.MonkeyPatch, settings: Any) -> list[Any]:
    registered: list[Any] = []
    django = types.ModuleType("django")
    django.__path__ = []  # type: ignore[attr-defined]
    conf = types.ModuleType("django.conf")
    conf.settings = settings  # type: ignore[attr-defined]
    core = types.ModuleType("django.core")
    core.__path__ = []  # type: ignore[attr-defined]
    checks = types.ModuleType("django.core.checks")

    class Warning:
        level = 30

        def __init__(self, msg: str, hint: str | None = None, id: str | None = None) -> None:
            self.msg = msg
            self.hint = hint
            self.id = id

    checks.Warning = Warning  # type: ignore[attr-defined]
    checks.register = registered.append  # type: ignore[attr-defined]
    for name, module in {
        "django": django,
        "django.conf": conf,
        "django.core": core,
        "django.core.checks": checks,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return registered


def _reset_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "_REGISTERED_APP_LABELS", set())


def test_compat_imports_without_django() -> None:
    source = (ROOT / "netbox_packer/compat.py").read_text(encoding="utf-8")
    assert not any(line.startswith(("import ", "from ")) and "django" in line for line in source.splitlines())


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ("4.5.7", "unsupported-old"),
        ("4.5.8", "stable"),
        ("4.6.99", "stable"),
        ("4.7.0", "stable"),
        ("4.7.0-beta2", "experimental"),
        ("4.7.99", "unsupported-new"),
    ],
)
def test_support_level_classifies_the_backward_compatible_range(release: str, expected: str) -> None:
    assert compat.netbox_support_level(release).value == expected


def test_declared_bounds_preserve_the_upgrade_floor_and_ga_ceiling() -> None:
    assert compat.CONTRACT_VERSION == "netbox-compat-v5"
    assert compat.STABLE_MIN_NETBOX_VERSION == "4.5.8"
    assert compat.STABLE_MAX_NETBOX_VERSION == "4.7.0"
    assert compat.PLUGIN_MIN_VERSION == "4.5.8"
    assert compat.PLUGIN_MAX_VERSION == "4.7.0"


def test_unparseable_version_fails_loudly() -> None:
    with pytest.raises(InvalidVersion):
        compat.netbox_support_level("not-a-version")


def test_detect_netbox_version_uses_release_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = types.SimpleNamespace(RELEASE=_Release("4.7.0", "4.7.0"), VERSION="fallback")
    _install_fake_django(monkeypatch, settings)
    assert compat.detect_netbox_version() == ("4.7.0", "4.7.0")


def test_detect_netbox_version_falls_back_to_settings_version(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = types.SimpleNamespace(RELEASE=object(), VERSION="4.6.6")
    _install_fake_django(monkeypatch, settings)
    assert compat.detect_netbox_version() == ("4.6.6", "4.6.6")


def test_detect_netbox_version_requires_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_django(monkeypatch, types.SimpleNamespace(RELEASE=object()))
    with pytest.raises(RuntimeError, match="Unable to determine"):
        compat.detect_netbox_version()


def test_ga_registers_no_warning(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _reset_registration(monkeypatch)
    registered = _install_fake_django(monkeypatch, types.SimpleNamespace(RELEASE=_Release("4.7.0", "4.7.0")))
    with caplog.at_level(logging.WARNING):
        compat.register_netbox_compatibility_check(_AppConfig())
    assert registered[0]() == []
    assert not caplog.records


def test_prerelease_registers_one_advisory_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _reset_registration(monkeypatch)
    registered = _install_fake_django(
        monkeypatch,
        types.SimpleNamespace(RELEASE=_Release("4.7.0", "4.7.0-beta2", "beta2")),
    )
    with caplog.at_level(logging.WARNING):
        compat.register_netbox_compatibility_check(_AppConfig())
    results = registered[0]()
    assert len(results) == 1
    assert results[0].id == "netbox_packer.W001"
    assert results[0].level == 30
    assert "experimental basis only" in results[0].msg
    assert any("experimental basis only" in record.getMessage() for record in caplog.records)


def test_registration_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_registration(monkeypatch)
    registered = _install_fake_django(monkeypatch, types.SimpleNamespace(RELEASE=_Release("4.7.0", "4.7.0")))
    compat.register_netbox_compatibility_check(_AppConfig())
    compat.register_netbox_compatibility_check(_AppConfig())
    assert len(registered) == 1


@pytest.mark.parametrize(
    ("display", "designation", "expected"),
    [("4.7.0-beta2", None, True), ("4.7.0", None, False), ("4.7.0", "beta2", True)],
)
def test_prerelease_detection(display: str, designation: str | None, expected: bool) -> None:
    assert compat.is_prerelease_netbox(display, designation) is expected
