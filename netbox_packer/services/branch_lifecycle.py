"""Optional netbox-branching lifecycle wrappers for netbox-packer jobs."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from netbox_packer.models import PackerPluginSettings

logger = logging.getLogger("netbox_packer.branch_lifecycle")

_BRANCHING_UNAVAILABLE = (
    "Branch lifecycle support requires netbox-proxbox with its "
    "netbox_proxbox.services.branch_lifecycle helpers installed."
)


class BranchingUnavailableError(RuntimeError):
    """Branch isolation was requested but cannot be provided safely."""


def _exception_reason(prefix: str, exc: Exception) -> str:
    """Return a concise reason that retains the exception type."""

    detail = str(exc).strip()
    suffix = f": {detail}" if detail else ""
    return f"{prefix} ({type(exc).__name__}{suffix})"


def _proxbox_branch_lifecycle() -> Any | None:
    try:
        from netbox_proxbox.services import branch_lifecycle  # noqa: PLC0415
    except Exception:
        logger.exception("Could not import netbox-proxbox branch lifecycle helpers")
        return None
    return branch_lifecycle


def _legacy_unavailable_reason(lifecycle: Any) -> str | None:
    """Check runtime availability through the pre-0.0.27 helper contract."""

    checker = getattr(lifecycle, "is_branching_available", None)
    if not callable(checker):
        return "the netbox-proxbox availability helper is missing"
    try:
        available = bool(checker())
    except Exception as exc:
        return _exception_reason("the branching runtime check failed", exc)
    if available:
        return None
    return "netbox-proxbox reported that the netbox-branching runtime is unavailable"


def _typed_unavailable_reason(lifecycle: Any, resolver: Any) -> str | None:
    """Interpret the netbox-proxbox 0.0.27 branching decision."""

    if not callable(resolver):
        return "netbox-proxbox resolve_branching_decision is not callable"
    try:
        decision = resolver()
    except Exception as exc:
        return _exception_reason("the branching decision could not be resolved", exc)
    state = getattr(getattr(decision, "state", None), "value", None)
    if state == "enabled":
        return None
    if state == "configured_but_unavailable":
        return getattr(decision, "reason", None) or "netbox-branching is unavailable"
    if state == "disabled":
        return _legacy_unavailable_reason(lifecycle)
    return f"netbox-proxbox returned an unknown branching decision state ({state!r})"


def _branching_unavailable_reason(lifecycle: Any) -> str | None:
    """Return a failure reason, preferring the typed 0.0.27 decision."""

    resolver = getattr(lifecycle, "resolve_branching_decision", None)
    if resolver is None:
        return _legacy_unavailable_reason(lifecycle)
    return _typed_unavailable_reason(lifecycle, resolver)


def _branching_failure_message(reason: str) -> str:
    """Build the actionable refusal shown by the failed NetBox job."""

    return (
        "Packer staleness check refused: branch isolation is configured with "
        "PackerPluginSettings.branching_enabled=True, but the netbox-branching "
        f"runtime is unavailable ({reason}). Install and enable a compatible "
        "netbox-branching runtime, or set branching_enabled=False to explicitly "
        "allow staleness writes to main."
    )


def is_branching_available() -> bool:
    lifecycle = _proxbox_branch_lifecycle()
    if lifecycle is None:
        return False
    try:
        return bool(lifecycle.is_branching_available())
    except Exception:
        logger.exception("Could not determine netbox-branching availability")
        return False


def create_and_provision_branch(
    *,
    name: str,
    user: Any | None,
    ready_timeout_seconds: int = 60,
) -> Any:
    lifecycle = _proxbox_branch_lifecycle()
    if lifecycle is None:
        raise NotImplementedError(_BRANCHING_UNAVAILABLE)
    return lifecycle.create_and_provision_branch(
        name=name,
        user=user,
        ready_timeout_seconds=ready_timeout_seconds,
    )


@contextmanager
def activate_branch_context(branch: Any):
    """Activate a Branch for all ORM calls within the context."""

    from netbox_branching.utilities import activate_branch  # noqa: PLC0415

    with activate_branch(branch):
        yield


def branch_has_conflicts(branch: Any) -> bool:
    lifecycle = _proxbox_branch_lifecycle()
    if lifecycle is None:
        raise NotImplementedError(_BRANCHING_UNAVAILABLE)
    return bool(lifecycle.branch_has_conflicts(branch))


def merge_branch(
    *,
    branch: Any,
    user: Any | None,
    on_conflict: str,
) -> tuple[bool, str]:
    lifecycle = _proxbox_branch_lifecycle()
    if lifecycle is None:
        raise NotImplementedError(_BRANCHING_UNAVAILABLE)
    result = lifecycle.merge_branch(
        branch=branch,
        user=user,
        on_conflict=on_conflict,
    )
    return bool(result[0]), str(result[1])


def branching_enabled_settings() -> dict[str, str] | None:
    """Return Packer branching config, or None only when explicitly disabled."""

    try:
        settings_obj = PackerPluginSettings.get_solo()
        branching_enabled = settings_obj.branching_enabled
    except Exception as exc:
        logger.exception("Could not load PackerPluginSettings")
        reason = _exception_reason("Packer plugin settings could not be loaded", exc)
        raise BranchingUnavailableError(
            "Packer staleness check refused: branching_enabled could not be read, "
            f"so branch isolation cannot be safely ruled out ({reason}). Restore "
            "access to PackerPluginSettings before retrying the staleness check."
        ) from exc
    if branching_enabled is False:
        return None
    if branching_enabled is not True:
        raise BranchingUnavailableError(
            "Packer staleness check refused: PackerPluginSettings.branching_enabled "
            "did not contain a boolean value, so branch isolation cannot be safely "
            "ruled out."
        )
    lifecycle = _proxbox_branch_lifecycle()
    if lifecycle is None:
        raise BranchingUnavailableError(_branching_failure_message(_BRANCHING_UNAVAILABLE))
    unavailable_reason = _branching_unavailable_reason(lifecycle)
    if unavailable_reason is not None:
        raise BranchingUnavailableError(_branching_failure_message(unavailable_reason))
    return {
        "prefix": getattr(settings_obj, "branch_name_prefix", "") or "packer-stale",
        "on_conflict": getattr(settings_obj, "branch_on_conflict", "") or "fail",
    }


__all__ = (
    "BranchingUnavailableError",
    "activate_branch_context",
    "branch_has_conflicts",
    "branching_enabled_settings",
    "create_and_provision_branch",
    "is_branching_available",
    "merge_branch",
)
