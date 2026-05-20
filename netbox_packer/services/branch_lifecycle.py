"""Optional netbox-branching lifecycle wrappers for netbox-packer jobs."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("netbox_packer.branch_lifecycle")

_BRANCHING_UNAVAILABLE = (
    "Branch lifecycle support requires the netbox-branching plugin to be installed."
)

__all__ = (
    "activate_branch_context",
    "branch_has_conflicts",
    "branching_enabled_settings",
    "create_and_provision_branch",
    "is_branching_available",
    "merge_branch",
)


def is_branching_available() -> bool:
    try:
        import netbox_branching  # noqa: F401, PLC0415
    except Exception:
        return False
    return True


def branching_enabled_settings() -> dict[str, str] | None:
    """Return Packer branching config, or ``None`` when disabled/unavailable."""
    if not is_branching_available():
        return None
    try:
        from netbox_packer.models import PackerPluginSettings  # noqa: PLC0415
        settings_obj = PackerPluginSettings.get_solo()
    except Exception:
        logger.exception("Could not load PackerPluginSettings")
        return None
    if not getattr(settings_obj, "branching_enabled", False):
        return None
    return {
        "prefix": getattr(settings_obj, "branch_name_prefix", "") or "packer-stale",
        "on_conflict": getattr(settings_obj, "branch_on_conflict", "") or "fail",
    }


def create_and_provision_branch(
    *,
    name: str,
    user: Any | None,
    ready_timeout_seconds: int = 60,
) -> Any:
    """Create a Branch, provision it synchronously, and return it when READY."""
    from netbox_branching.choices import BranchStatusChoices  # noqa: PLC0415
    from netbox_branching.models import Branch  # noqa: PLC0415

    branch = Branch(name=name)
    branch.save(provision=False)
    try:
        branch.provision(user=user)
    except Exception:
        logger.exception("Branch provision failed for %s", name)
        raise

    deadline = time.monotonic() + ready_timeout_seconds
    while True:
        branch.refresh_from_db()
        if branch.status == BranchStatusChoices.READY:
            return branch
        if branch.status == BranchStatusChoices.FAILED:
            raise RuntimeError(
                f"Branch {branch.name} entered FAILED status during provisioning"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Branch {branch.name} did not reach READY within "
                f"{ready_timeout_seconds}s (status={branch.status})"
            )
        time.sleep(0.5)


@contextmanager
def activate_branch_context(branch: Any):
    """Context manager that activates a Branch for all ORM calls within the block."""
    from netbox_branching.utilities import activate_branch  # noqa: PLC0415
    with activate_branch(branch):
        yield


def branch_has_conflicts(branch: Any) -> bool:
    """True when ChangeDiff rows for this branch contain unresolved conflicts."""
    from netbox_branching.models import ChangeDiff  # noqa: PLC0415
    return ChangeDiff.objects.filter(branch=branch, conflicts__isnull=False).exists()


def merge_branch(
    *,
    branch: Any,
    user: Any | None,
    on_conflict: str,
) -> tuple[bool, str]:
    """Apply branch conflict policy and merge in-process.

    Returns (merged, message). When conflicts exist and policy is ``fail``,
    the branch is left in READY for operator inspection. When policy is
    ``acknowledge``, the merge proceeds despite conflicts.
    """
    if branch_has_conflicts(branch):
        if on_conflict != "acknowledge":
            return False, (
                f"Branch {branch.name} has unresolved conflicts and "
                "branch_on_conflict=fail; leaving branch open."
            )
        logger.warning(
            "Merging %s despite conflicts (branch_on_conflict=acknowledge)",
            branch.name,
        )

    try:
        branch.merge(user=user)
    except Exception as exc:
        logger.exception("Branch merge raised for %s", branch.name)
        return False, f"merge failed: {exc}"
    return True, f"Branch {branch.name} merged."
