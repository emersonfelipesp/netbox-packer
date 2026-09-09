"""Regression tests for the retired pre-GA release identity hook."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_compat_module():
    spec = importlib.util.spec_from_file_location(
        "netbox_packer_release_identity_under_test", ROOT / "netbox_packer/compat.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compat = _load_compat_module()


def test_release_identity_hook_is_a_noop_for_ga() -> None:
    compat.validate_held_netbox_release_identity(object, "4.7.0")


def test_release_identity_hook_is_a_noop_for_legacy_callers() -> None:
    compat.validate_held_netbox_release_identity(object, "4.7.0-beta2")
