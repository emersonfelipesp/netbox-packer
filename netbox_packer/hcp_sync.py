"""HCP Packer Registry sync — import artifact metadata into NetBox."""

import logging
from urllib.parse import urljoin

logger = logging.getLogger("netbox_packer.hcp_sync")

HCP_AUTH_URL = "https://auth.idp.hashicorp.com/oauth2/token"
HCP_API_BASE = "https://api.cloud.hashicorp.com/packer/2023-01-01"


def _get_plugin_setting(key, default=None):
    from django.conf import settings

    return settings.PLUGINS_CONFIG.get("netbox_packer", {}).get(key, default)


def _is_hcp_configured():
    """Return True if all required HCP Packer settings are present and non-empty."""
    required = ("HCP_CLIENT_ID", "HCP_CLIENT_SECRET", "HCP_ORGANIZATION_ID", "HCP_PROJECT_ID")
    return all(_get_plugin_setting(k) for k in required)


def _get_hcp_token():
    """Obtain an OAuth2 Bearer token from HCP using client credentials flow."""
    import json
    import urllib.parse
    import urllib.request

    client_id = _get_plugin_setting("HCP_CLIENT_ID")
    client_secret = _get_plugin_setting("HCP_CLIENT_SECRET")

    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "audience": "https://api.hashicorp.cloud",
        }
    ).encode()

    req = urllib.request.Request(
        HCP_AUTH_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    return payload["access_token"]


def _hcp_get(path, token):
    """Perform a GET request against the HCP Packer Registry API."""
    import json
    import urllib.request

    org = _get_plugin_setting("HCP_ORGANIZATION_ID")
    project = _get_plugin_setting("HCP_PROJECT_ID")
    base = f"{HCP_API_BASE}/organizations/{org}/projects/{project}/images"
    url = urljoin(base + "/", path.lstrip("/"))

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def sync_template_from_hcp(template, token):
    """
    Sync a single PackerTemplate from HCP Packer Registry.

    Reads the latest iteration from the template's hcp_bucket_name/hcp_channel_name.
    Skips if iteration_id is unchanged (idempotent).
    Extracts proxmox_template_id from build labels["vmid"] if present.

    Returns: dict with {"updated": bool, "reason": str}
    """
    from django.utils import timezone

    bucket = template.hcp_bucket_name
    channel = template.hcp_channel_name or "latest"

    if not bucket:
        return {"updated": False, "reason": "hcp_bucket_name not set"}

    try:
        channel_data = _hcp_get(f"{bucket}/channels/{channel}", token)
    except Exception as exc:
        logger.warning(
            "HCP sync: could not fetch channel '%s' for bucket '%s': %s",
            channel,
            bucket,
            exc,
        )
        return {"updated": False, "reason": f"fetch error: {exc}"}

    iteration = channel_data.get("channel", {}).get("pointer", {})
    iteration_id = iteration.get("iteration_id") or channel_data.get("channel", {}).get("iteration_id")

    if not iteration_id:
        return {"updated": False, "reason": "no iteration_id in channel response"}

    if template.hcp_iteration_id == iteration_id:
        return {"updated": False, "reason": "iteration unchanged"}

    # Fetch build details to extract vmid label
    try:
        iteration_data = _hcp_get(f"{bucket}/iterations/{iteration_id}", token)
    except Exception as exc:
        logger.warning("HCP sync: could not fetch iteration '%s': %s", iteration_id, exc)
        iteration_data = {}

    builds = iteration_data.get("iteration", {}).get("builds", [])
    vmid = None
    build_id = None
    for build in builds:
        labels = build.get("labels") or {}
        if labels.get("vmid"):
            vmid = labels["vmid"]
        build_id = build.get("id") or build_id

    update_fields = {
        "hcp_iteration_id": iteration_id,
        "hcp_last_synced_at": timezone.now(),
    }
    if build_id:
        update_fields["hcp_build_id"] = build_id
    if vmid:
        try:
            update_fields["proxmox_template_id"] = int(vmid)
        except (ValueError, TypeError):
            logger.warning("HCP sync: invalid vmid label '%s' for bucket '%s'", vmid, bucket)

    from .models import PackerTemplate

    PackerTemplate.objects.filter(pk=template.pk).update(**update_fields)
    logger.info(
        "HCP sync: updated template '%s' — iteration %s, vmid=%s",
        template.name,
        iteration_id,
        vmid,
    )
    return {"updated": True, "iteration_id": iteration_id, "vmid": vmid}


class PackerHCPSyncJob:
    """
    RQ background job that polls HCP Packer Registry and syncs artifact metadata.

    Registered as a JobRunner subclass so it can be enqueued via NetBox.
    Gracefully skips if HCP credentials are not configured.
    """

    def run(self, *args, **kwargs):
        if not _is_hcp_configured():
            logger.debug("PackerHCPSyncJob: HCP_CLIENT_ID not configured — skipping sync.")
            return

        try:
            token = _get_hcp_token()
        except Exception as exc:
            logger.error("PackerHCPSyncJob: failed to obtain HCP token: %s", exc)
            raise

        from .models import PackerTemplate

        templates = PackerTemplate.objects.exclude(hcp_bucket_name="")
        updated = 0
        skipped = 0

        for template in templates:
            result = sync_template_from_hcp(template, token)
            if result.get("updated"):
                updated += 1
            else:
                skipped += 1

        logger.info("PackerHCPSyncJob complete: %d updated, %d skipped", updated, skipped)


try:
    from netbox.jobs import JobRunner

    class PackerHCPSyncJobRunner(PackerHCPSyncJob, JobRunner):
        """JobRunner variant of PackerHCPSyncJob for RQ scheduling via NetBox."""

        class Meta:
            name = "Packer HCP Registry Sync"

        def run(self, *args, **kwargs):
            PackerHCPSyncJob.run(self, *args, **kwargs)

except ImportError:
    PackerHCPSyncJobRunner = None
