"""RQ background jobs for netbox-packer."""

import logging
import subprocess
from datetime import timedelta

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


def _resolve_cloud_image_url(template, overrides):
    """Resolve the base cloud image URL for a cloud_config build.

    Honors ``variable_overrides['image_url']`` first, then derives a sensible
    default from ``os_family`` / ``os_version``.
    """
    override = (overrides or {}).get("image_url")
    if override:
        return str(override)
    fam = (template.os_family or "").lower()
    ver = (template.os_version or "").strip()
    if fam == "ubuntu" and ver:
        return f"https://cloud-images.ubuntu.com/releases/{ver}/release/ubuntu-{ver}-server-cloudimg-amd64.img"
    if fam == "debian":
        return "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
    raise RuntimeError(
        f"No base cloud image URL for os_family={fam!r} os_version={ver!r}; "
        "set variable_overrides['image_url'] on the build."
    )


def _resolve_ssh_host(template, overrides):
    """Resolve the Proxmox node host that proxbox-api should SSH into for the bake.

    Honors ``variable_overrides['ssh_host']`` first, then the hostname of the
    template's ``proxmox_endpoint`` (the netbox-proxbox ProxmoxEndpoint URL),
    then ``proxmox_node``.
    """
    override = (overrides or {}).get("ssh_host")
    if override:
        return str(override)
    endpoint = (template.proxmox_endpoint or "").strip()
    if endpoint:
        from urllib.parse import urlparse

        host = urlparse(endpoint).hostname
        if host:
            return host
    return template.proxmox_node or None


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

        installer = template.installer_config
        is_cloud_config = installer is not None and installer.installer_type == "cloud_config"

        try:
            if is_cloud_config:
                # Cloud-init template image: delegate the real Proxmox bake to proxbox-api,
                # which writes installer_config.content as a cicustom user snippet over SSH.
                self._run_proxbox_cloud_build(build, template, node, timeout)
            else:
                self._run_packer(build, template, endpoint, node, timeout)
        except Exception as exc:
            logger.exception("PackerBuildJob failed for build #%s", build_id)
            build.status = "failed"
            build.finished_at = timezone.now()
            build.log = f"{build.log or ''}\n\n[ERROR] {exc}".strip()
            build.save(update_fields=["status", "finished_at", "log"])
            PackerTemplate.objects.filter(pk=template.pk).update(build_status="failed")
            raise

    def _run_proxbox_cloud_build(self, build, template, node, timeout):
        """Bake a cloud-init template image by delegating to proxbox-api."""
        from .models import PackerPluginSettings, PackerTemplate
        from .proxbox_client import ProxboxApiError, call_proxbox_build

        settings_row = PackerPluginSettings.get_solo()
        api_url = (settings_row.proxbox_api_url or "").strip()
        installer = template.installer_config
        storage = template.storage_pool or "local"
        target_node = node or template.proxmox_node or ""
        image_url = _resolve_cloud_image_url(template, build.variable_overrides)
        ssh_host = _resolve_ssh_host(template, build.variable_overrides)

        log_lines = [
            f"[INFO] Cloud-init template image build for '{template.name}'",
            f"[INFO] Delegating real Proxmox bake to proxbox-api: {api_url or 'UNSET'}",
            f"[INFO] Installer config: {installer} ({installer.installer_type})",
            f"[INFO] Base image: {image_url}",
            f"[INFO] Proxmox SSH host: {ssh_host or 'UNSET'} | storage: {storage}",
        ]

        if not api_url:
            build.log = "\n".join(log_lines)
            build.save(update_fields=["log"])
            raise RuntimeError(
                "PackerPluginSettings.proxbox_api_url is not configured; cannot bake a "
                "cloud_config template image via proxbox-api."
            )

        try:
            response = call_proxbox_build(
                proxbox_api_url=api_url,
                proxbox_api_key=settings_row.get_proxbox_api_key(),
                name=template.name,
                vmid=template.proxmox_template_id,
                target_node=target_node,
                image_url=image_url,
                user_data_yaml=installer.content,
                image_storage=storage,
                vm_storage=storage,
                storage=storage,
                snippets_storage=storage,
                ssh_host=ssh_host,
                timeout=int(timeout) + 300,
            )
        except ProxboxApiError as exc:
            log_lines.append(f"[ERROR] {exc}")
            build.log = "\n".join(log_lines)
            build.save(update_fields=["log"])
            raise RuntimeError(str(exc)) from exc

        status = str(response.get("status", "")).lower()
        result_vmid = response.get("vmid") or response.get("template_vmid")
        log_lines.append(f"[INFO] proxbox-api status: {status or 'unknown'} (vmid={result_vmid})")
        for key in ("build_script", "stdout", "stderr"):
            value = response.get(key)
            if value:
                log_lines.append(f"[{key.upper()}]\n{value}")

        build.finished_at = timezone.now()
        if status in {"created", "completed", "already_exists"}:
            build.status = "success"
            build.exit_code = 0
            if result_vmid:
                build.result_template_id = int(result_vmid)
            update = {"build_status": "ready", "built_at": timezone.now()}
            if installer:
                update["installer_config_checksum_at_build"] = installer.checksum
            PackerTemplate.objects.filter(pk=template.pk).update(**update)
        else:
            build.status = "failed"
            build.exit_code = response.get("returncode") or 1
            PackerTemplate.objects.filter(pk=template.pk).update(build_status="failed")

        build.log = "\n".join(log_lines)
        build.save(update_fields=["status", "finished_at", "exit_code", "result_template_id", "log"])

    def _run_packer(self, build, template, endpoint, node, timeout):
        """Run packer init + packer build, streaming output into build.log."""
        from .models import PackerTemplate

        template_ref = template.packer_template_ref
        if not template_ref:
            raise ValueError(
                f"PackerTemplate #{template.pk} has no packer_template_ref set; "
                "cannot determine which .pkr.hcl file to build."
            )

        log_lines = [f"[INFO] Starting Packer build for template '{template.name}'"]
        log_lines.append(f"[INFO] Template ref: {template_ref}")
        log_lines.append(f"[INFO] Target node: {node}")

        # Build variable overrides from: per-run overrides → template fields → defaults
        var_args = _build_var_args(template, build.variable_overrides, endpoint, node)

        exit_code = self._run_subprocess(["packer", "init", template_ref], build, log_lines, timeout, phase="init")
        if exit_code != 0:
            raise RuntimeError(f"packer init exited with code {exit_code}")

        exit_code = self._run_subprocess(
            ["packer", "build"] + var_args + [template_ref], build, log_lines, timeout, phase="build"
        )

        build.exit_code = exit_code
        if exit_code == 0:
            build.status = "success"
            build.finished_at = timezone.now()
            if template.installer_config:
                build.template.installer_config_checksum_at_build = template.installer_config.checksum
                build.template.save(update_fields=["installer_config_checksum_at_build"])
            PackerTemplate.objects.filter(pk=template.pk).update(
                build_status="ready",
                built_at=timezone.now(),
            )
        else:
            build.status = "failed"
            build.finished_at = timezone.now()
            PackerTemplate.objects.filter(pk=template.pk).update(build_status="failed")

        build.log = "\n".join(log_lines)
        build.save(update_fields=["status", "finished_at", "exit_code", "log"])

    def _run_subprocess(self, cmd, build, log_lines, timeout, phase="build"):
        """Run a subprocess, capturing output into log_lines with partial saves."""
        log_lines.append(f"[INFO] Running: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            log_lines.append("[ERROR] packer executable not found in PATH")
            return 127

        deadline = timezone.now() + timedelta(seconds=timeout)

        for line_count, line in enumerate(proc.stdout, start=1):
            log_lines.append(line.rstrip())
            # Partial save every 50 lines so logs appear incrementally
            if line_count % 50 == 0:
                build.log = "\n".join(log_lines)
                build.save(update_fields=["log"])
            if timezone.now() > deadline:
                proc.kill()
                log_lines.append(f"[ERROR] Timeout exceeded ({timeout}s) during {phase}")
                return 124

        proc.wait()
        return proc.returncode


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


def dispatch_build(build):
    """Enqueue a PackerBuildJob (RQ background) for an existing PackerBuild.

    Logs and re-raises on failure so callers can decide how to surface it. This
    is the single dispatch point used by both the REST API and the HTML build
    action, fixing the gap where creating a PackerBuild never started a job.
    """
    try:
        PackerBuildJob.enqueue(instance=build, build_id=build.pk)
    except Exception:
        logger.exception("Failed to enqueue PackerBuildJob for build #%s", build.pk)
        raise
