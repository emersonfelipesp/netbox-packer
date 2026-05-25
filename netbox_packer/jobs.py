"""RQ background jobs for netbox-packer."""

import logging

from django.conf import settings
from django.utils import timezone
from netbox.jobs import JobRunner

logger = logging.getLogger("netbox_packer.jobs")

# Minimum CPU arch requirements known to require non-default cpu_type
MIN_CPU_KNOWN_REQUIREMENTS = {
    "rhel9": "Nehalem",
    "rhel10": "Nehalem",
    "almalinux9": "Nehalem",
    "almalinux10": "Nehalem",
    "rocky9": "Nehalem",
    "rocky10": "Nehalem",
}


def _get_plugin_setting(key, default=None):
    """Read a setting from NetBox plugin config with fall-through to defaults."""
    plugin_cfg = settings.PLUGINS_CONFIG.get("netbox_packer", {})
    return plugin_cfg.get(key, default)


def select_build_node(template, skip_affinity_check=False):
    """
    Select the best build node for a template from its PackerBuildTarget list.

    Resolution order: enabled targets sorted by priority (ascending).
    A target is skipped when:
    - It is at MAX_CONCURRENT_BUILDS_PER_NODE active builds, OR
    - NodeAffinityValidator reports hard errors for the target node (unless
      skip_affinity_check=True is passed).

    Falls back to (template.proxmox_endpoint, template.proxmox_node) when no
    PackerBuildTarget records exist.

    Returns (proxmox_endpoint, proxmox_node) tuple.
    """
    from .models import PackerBuild
    from .validators import NodeAffinityValidator

    max_concurrent = _get_plugin_setting("MAX_CONCURRENT_BUILDS_PER_NODE", 2)
    targets = list(template.build_targets.filter(enabled=True).order_by("priority"))

    if not targets:
        # No multi-cluster targets — fall back to template primary node
        return template.proxmox_endpoint, template.proxmox_node

    for target in targets:
        # Capacity check
        active_count = PackerBuild.objects.filter(
            selected_node=target.proxmox_node,
            status__in=("queued", "running"),
        ).count()
        if active_count >= max_concurrent:
            logger.debug(
                "select_build_node: skipping '%s' — at capacity (%d/%d)",
                target.proxmox_node,
                active_count,
                max_concurrent,
            )
            continue

        # Affinity check (skip gracefully on Proxmox connectivity issues)
        if not skip_affinity_check:
            # Temporarily override template fields with target's endpoint/node
            _orig_endpoint = template.proxmox_endpoint
            _orig_node = template.proxmox_node
            template.proxmox_endpoint = target.proxmox_endpoint
            template.proxmox_node = target.proxmox_node
            try:
                validator = NodeAffinityValidator(template)
                is_valid, errors, warnings = validator.validate()
            finally:
                template.proxmox_endpoint = _orig_endpoint
                template.proxmox_node = _orig_node

            if not is_valid:
                logger.debug(
                    "select_build_node: skipping '%s' — affinity check failed: %s",
                    target.proxmox_node,
                    errors,
                )
                continue

        return target.proxmox_endpoint, target.proxmox_node

    # All targets exhausted — fall back to template primary node
    logger.warning(
        "select_build_node: no suitable target found for template '%s'; falling back to primary node '%s'",
        template.name,
        template.proxmox_node,
    )
    return template.proxmox_endpoint, template.proxmox_node


class PackerBuildJob(JobRunner):
    """
    Execute a Packer build asynchronously.

    Runs `packer init` (idempotent) then `packer build` as a subprocess,
    streams output into PackerBuild.log incrementally, and updates
    PackerTemplate.build_status on completion.
    """

    class Meta:
        name = "Packer Build"

    def run(self, *args, **kwargs):
        """
        Expected kwargs:
            build_id (int): primary key of the PackerBuild record to execute.
        """
        from .models import PackerBuild, PackerTemplate

        build_id = kwargs.get("build_id")
        if not build_id:
            raise ValueError("PackerBuildJob requires build_id kwarg")

        try:
            build = PackerBuild.objects.select_related("template").get(pk=build_id)
        except PackerBuild.DoesNotExist:
            raise ValueError(f"PackerBuild #{build_id} not found")

        template = build.template
        timeout = _get_plugin_setting("PACKER_BUILD_TIMEOUT_SECONDS", 3600)

        # Mark build as running
        build.status = "running"
        build.started_at = timezone.now()
        build.save(update_fields=["status", "started_at"])

        # Select build node
        endpoint, node = select_build_node(template)
        build.selected_node = node or ""
        build.save(update_fields=["selected_node"])

        try:
            self._run_packer(build, template, endpoint, node, timeout)
        except Exception as exc:
            logger.exception("PackerBuildJob failed for build #%s", build_id)
            build.status = "failed"
            build.finished_at = timezone.now()
            build.log += f"\n\n[ERROR] {exc}"
            build.save(update_fields=["status", "finished_at", "log"])
            PackerTemplate.objects.filter(pk=template.pk).update(build_status="failed")
            raise

    def _run_packer(self, build, template, endpoint, node, timeout):
        """Delegate the Packer build to proxbox-api and consume the SSE stream."""
        from .models import PackerPluginSettings, PackerTemplate
        from .proxbox_client import (
            ProxboxAPIError,
            cancel_build,
            map_status,
            resolve_endpoint_id,
            start_build,
            stream_build,
        )

        settings = PackerPluginSettings.get_solo()
        proxbox_url = settings.proxbox_api_url
        api_key = settings.proxbox_api_key

        if not proxbox_url:
            raise ValueError(
                "PackerPluginSettings.proxbox_api_url is not configured; "
                "cannot delegate build to proxbox-api."
            )

        log_lines = [f"[INFO] Delegating build to proxbox-api at {proxbox_url}"]
        log_lines.append(f"[INFO] Template: {template.name}, node: {node}")

        endpoint_url = endpoint if isinstance(endpoint, str) else str(endpoint)
        try:
            endpoint_id = resolve_endpoint_id(proxbox_url, endpoint_url)
        except ProxboxAPIError as exc:
            raise RuntimeError(
                f"Failed to resolve Proxmox endpoint URL '{endpoint_url}' "
                f"to an endpoint_id on proxbox-api: {exc}"
            ) from exc

        payload = {
            "endpoint_id": endpoint_id,
            "target_node": node or template.proxmox_node,
            "output_vmid": template.proxmox_template_id,
            "output_name": template.name,
            "os_family": template.os_family,
            "os_release": template.os_version,
            "image_version": template.os_version,
            "vm_storage": template.storage_pool or "local-lvm",
            "provisioner_recipe": template.packer_template_ref or "default",
        }
        if template.min_cpu_type:
            payload["cpu_type"] = template.min_cpu_type
        if build.variable_overrides:
            payload["variables"] = build.variable_overrides

        try:
            build_id = start_build(proxbox_url, api_key, payload)
        except ProxboxAPIError as exc:
            raise RuntimeError(f"proxbox-api rejected the build request: {exc}") from exc

        log_lines.append(f"[INFO] proxbox-api build_id: {build_id}")
        overrides = dict(build.variable_overrides or {})
        overrides["_proxbox_build_id"] = build_id
        build.variable_overrides = overrides
        build.save(update_fields=["variable_overrides"])

        final_status = "failed"
        exit_code = None

        try:
            for line_count, sse in enumerate(stream_build(proxbox_url, api_key, build_id, timeout=timeout), 1):
                if sse.event == "packer_log":
                    line = sse.data if isinstance(sse.data, str) else str(sse.data)
                    log_lines.append(line)
                elif sse.event in ("build_completed", "build_success"):
                    final_status = "success"
                elif sse.event == "build_failed":
                    final_status = "failed"
                    if isinstance(sse.data, dict):
                        exit_code = sse.data.get("exit_code")
                elif sse.event == "build_cancelled":
                    final_status = "cancelled"
                elif sse.event == "status":
                    if isinstance(sse.data, dict):
                        s = sse.data.get("status", "")
                        mapped = map_status(s)
                        if mapped in ("success", "failed", "cancelled"):
                            final_status = mapped
                            if mapped == "failed":
                                exit_code = sse.data.get("exit_code")

                if line_count % 50 == 0:
                    build.log = "\n".join(log_lines)
                    build.save(update_fields=["log"])
        except Exception as exc:
            log_lines.append(f"[ERROR] SSE stream error: {exc}")
            final_status = "failed"

        build.exit_code = exit_code
        build.status = final_status
        build.finished_at = timezone.now()
        build.log = "\n".join(log_lines)
        build.save(update_fields=["status", "finished_at", "exit_code", "log"])

        if final_status == "success":
            if template.installer_config:
                template.installer_config_checksum_at_build = template.installer_config.checksum
                template.save(update_fields=["installer_config_checksum_at_build"])
            PackerTemplate.objects.filter(pk=template.pk).update(
                build_status="ready",
                built_at=timezone.now(),
            )
        else:
            PackerTemplate.objects.filter(pk=template.pk).update(build_status="failed")


def _build_var_args(template, overrides, endpoint, node):
    """
    Build packer -var flags.

    Variable resolution order: per-run overrides → template fields → defaults.
    """
    resolved = {}

    # Template fields as base
    if template.proxmox_node or node:
        resolved["proxmox_node"] = node or template.proxmox_node
    if template.storage_pool:
        resolved["proxmox_storage_pool"] = template.storage_pool
    if template.storage_pool_type:
        resolved["proxmox_storage_pool_type"] = template.storage_pool_type
    if template.storage_format:
        resolved["proxmox_storage_format"] = template.storage_format
    if template.min_cpu_type:
        resolved["cpu_type"] = template.min_cpu_type
    if endpoint:
        resolved["proxmox_url"] = endpoint.url if hasattr(endpoint, "url") else str(endpoint)

    # Per-run overrides win
    resolved.update(overrides)

    return [f"-var={k}={v}" for k, v in resolved.items()]


class PackerStalenessCheckJob(JobRunner):
    """
    Periodic job that marks stale templates and optionally queues rebuilds.

    Scheduled via PACKER_STALENESS_CHECK_INTERVAL plugin setting (cron expr).
    """

    class Meta:
        name = "Packer Staleness Check"

    def run(self, *args, **kwargs):
        from .models import PackerBuild, PackerTemplate
        from .services.branch_lifecycle import (
            activate_branch_context,
            branching_enabled_settings,
            create_and_provision_branch,
            merge_branch,
        )

        branch_config = branching_enabled_settings()
        branch = None
        if branch_config is not None:
            import uuid
            branch_name = f"{branch_config['prefix']}-{uuid.uuid4().hex[:8]}"
            try:
                branch = create_and_provision_branch(name=branch_name, user=None)
                logger.info("Staleness check: using branch '%s'", branch_name)
            except Exception:
                logger.exception("Branch provision failed; running staleness check on main")
                branch = None

        def _run_staleness(PackerBuild, PackerTemplate):
            checked = 0
            stale = 0
            queued = 0

            for template in PackerTemplate.objects.exclude(build_status__in=("building",)).exclude(max_age_days=None):
                checked += 1
                if not template.is_stale:
                    continue

                stale += 1
                PackerTemplate.objects.filter(pk=template.pk).update(build_status="stale")

                if not template.auto_rebuild:
                    continue

                # Only queue if no build is already active
                active = PackerBuild.objects.filter(template=template, status__in=("queued", "running")).exists()
                if active:
                    continue

                build = PackerBuild.objects.create(
                    template=template,
                    triggered_by="PackerStalenessCheckJob",
                    status="queued",
                )
                logger.info(
                    "Auto-queued rebuild for stale template '%s' (build #%s)",
                    template.name,
                    build.pk,
                )
                queued += 1

            logger.info(
                "Staleness check complete: %d templates checked, %d stale, %d rebuilds queued",
                checked,
                stale,
                queued,
            )

        if branch is not None:
            with activate_branch_context(branch):
                _run_staleness(PackerBuild, PackerTemplate)
            merged, msg = merge_branch(
                branch=branch,
                user=None,
                on_conflict=branch_config["on_conflict"],
            )
            if merged:
                logger.info("Staleness check branch merged: %s", msg)
            else:
                logger.warning("Staleness check branch merge failed: %s", msg)
        else:
            _run_staleness(PackerBuild, PackerTemplate)
