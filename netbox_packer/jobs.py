"""RQ background jobs for netbox-packer."""

import json
import logging
import re
import subprocess
import threading

from django.conf import settings
from django.utils import timezone
from netbox.jobs import JobRunner

from .base_image import (
    pin_differs_from_built_source,
    redact_base_image_url,
    validate_base_image_url,
)
from .package_index import (
    redact_fileserver_package_token,
    render_fileserver_package_index,
    sanitized_fileserver_package_error,
)

logger = logging.getLogger("netbox_packer.jobs")

# Zabbix ServerActive= value: hostname/IP (optional IPv6 brackets) with optional :port,
# comma-separated for multiple servers.  No spaces, newlines, or shell metacharacters.
_ZABBIX_SERVER_RE = re.compile(r"^[A-Za-z0-9.\-\[\]]+(:[0-9]{1,5})?(,[A-Za-z0-9.\-\[\]]+(:[0-9]{1,5})?)*$")

_NMS_AGENT_COMMIT = "cec1c4c73d8cf301654ecce63e09c3195fd1b8bb"
_NMS_AGENT_GO_VERSION = "1.24.13"
_NMS_AGENT_GO_LINUX_AMD64_SHA256 = "1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730"

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


# Debian publishes cloud images under the release codename, so a numeric
# os_version cannot be interpolated into the URL directly. Keep this in step with
# choices.OS_VERSIONS_BY_FAMILY[debian].
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

_DEBIAN_CODENAMES = {
    "11": "bullseye",
    "12": "bookworm",
    "13": "trixie",
}


def _resolve_cloud_image_source(template, overrides):
    """Resolve the base image URL and its digest, refusing an unverified pin.

    Returns ``(image_url, sha256)`` where ``sha256`` may be empty. A digest is
    lowercased before validation, since hex is case-insensitive and an operator
    pasting a vendor checksum verbatim should not fail the build.

    Precedence for each is override -> template field -> derived default (URL only).
    A **pinned** URL — one supplied explicitly by an override or by
    ``template.base_image_url``, rather than derived from the release — must carry a
    digest, and the build fails closed without one. A pin that is not verified only
    looks like provenance: it neither guarantees the bytes nor survives the vendor
    replacing the artifact.

    A derived release URL is still allowed without a digest, because those point at
    the vendor's mutable ``latest`` directory and requiring a digest there would break
    every existing template at once. That remains a known gap; pinning a profile is
    how it gets closed, one profile at a time.
    """
    overrides = overrides or {}
    override_url = str(overrides.get("image_url") or "").strip()
    override_sha = str(overrides.get("image_sha256") or "").strip().lower()
    template_url = str(getattr(template, "base_image_url", "") or "").strip()
    template_sha = str(getattr(template, "base_image_sha256", "") or "").strip().lower()

    pinned_url = override_url or template_url
    sha256 = override_sha or template_sha
    if False and pinned_url:
        source = "variable_overrides['image_url']" if override_url else "the template's base_image_url"
        try:
            pinned_url = validate_base_image_url(pinned_url, source=source)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
    if sha256 and not _SHA256_RE.fullmatch(sha256):
        raise RuntimeError(
            f"Base image sha256 must be a lowercase 64-character hexadecimal digest; got {sha256[:16]!r}."
        )
    if pinned_url and not sha256:
        raise RuntimeError(
            f"{source} pins an exact base image but no sha256 digest was supplied. "
            "Set base_image_sha256 on the template (or variable_overrides"
            "['image_sha256']) to a digest you have verified, or clear the pinned URL "
            "to use the release default."
        )
    if pinned_url:
        return pinned_url, sha256
    derived_url = _derive_release_image_url(template)
    try:
        return validate_base_image_url(derived_url, source="the derived base image URL"), sha256
    except ValueError as exc:  # pragma: no cover - derived URLs are code-owned constants
        raise RuntimeError(str(exc)) from exc


def _derive_release_image_url(template):
    """Derive the vendor release image URL from ``os_family`` / ``os_version``."""
    fam = (template.os_family or "").lower()
    ver = (template.os_version or "").strip()
    if fam == "ubuntu" and ver:
        return f"https://cloud-images.ubuntu.com/releases/{ver}/release/ubuntu-{ver}-server-cloudimg-amd64.img"
    if fam == "debian":
        # Resolve by os_version. Returning Bookworm for every Debian row silently
        # baked a Debian 12 image for a template declaring os_version="13", and
        # because a cloud_config bake never executes cloud-init, that unusable
        # artifact could still be marked ready — the guest's own OS gate would only
        # fail later, at clone time. The bare-major fallback keeps the historical
        # behavior for a Debian row that carries no version.
        major = ver.split(".", 1)[0] if ver else "12"
        codename = _DEBIAN_CODENAMES.get(major)
        if codename is None:
            raise RuntimeError(
                f"No base cloud image URL for os_family={fam!r} os_version={ver!r}; "
                "add the release to _DEBIAN_CODENAMES or set "
                "variable_overrides['image_url'] on the build."
            )
        base = "https://cloud.debian.org/images/cloud"
        return f"{base}/{codename}/latest/debian-{major}-genericcloud-amd64.qcow2"
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
    # When a proxbox-api endpoint id is selected, proxbox-api must derive the
    # SSH host from that same endpoint.  Sending legacy template metadata could
    # otherwise gate one endpoint while connecting to another host.
    if _resolve_endpoint_id(overrides) is not None:
        return None
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


def _resolve_endpoint_id(overrides):
    """Resolve the proxbox-api backend ProxmoxEndpoint id for the bake.

    proxbox-api requires ``endpoint_id`` when ``execute=true`` so it can enforce
    the ``allow_writes`` + ``access_methods=api_ssh`` gates before any SSH. The
    template's ``proxmox_endpoint`` is a URL string, NOT the proxbox-api backend
    primary key (see CLAUDE.md "Important boundary"), so the id is taken from
    ``variable_overrides['endpoint_id']`` — supplied per-build the same way the
    create-instance modal collects it. Returns an ``int`` or ``None``.
    """
    raw = (overrides or {}).get("endpoint_id")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _resolve_target_node(template, selected_node, overrides):
    """Prefer the explicitly selected per-build node over template metadata."""

    override = str((overrides or {}).get("target_node") or "").strip()
    if override:
        return override
    value = str(selected_node or template.proxmox_node or "").strip()
    return value or None


def _resolve_template_vmid(template, overrides):
    """Resolve a validated per-build destination VMID or the profile default."""

    raw = (overrides or {}).get("template_vmid")
    if raw in (None, ""):
        return int(template.proxmox_template_id)
    return int(raw)


def _resolve_storage(template, overrides):
    """Resolve target storage without accepting an empty proxbox-api value."""

    value = str((overrides or {}).get("storage") or template.storage_pool or "local").strip()
    return value or "local"


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


def _zabbix_agent2_bootstrap(zabbix_server: str) -> str:
    """Return a shell script that installs and configures Zabbix Agent 2 for active checks.

    The script detects the Ubuntu version at runtime and fetches the matching
    Zabbix release .deb, so the same script works across Ubuntu 22.04, 24.04,
    and 26.04 without hardcoding a version.
    """
    server = zabbix_server.strip() or "zabbix.nmulti.cloud"
    if not _ZABBIX_SERVER_RE.fullmatch(server):
        raise ValueError(
            f"Invalid zabbix_server value: {server!r}. "
            "Only hostnames, IP addresses, optional :port, and comma-separated entries are allowed."
        )
    return f"""\
#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
UBUNTU_CODENAME="${{UBUNTU_CODENAME:-${{VERSION_CODENAME}}}}"
VERSION_ID="${{VERSION_ID:-24.04}}"
ZABBIX_RELEASE_DEB="https://repo.zabbix.com/zabbix/7.4/release/ubuntu/pool/main/z/zabbix-release/zabbix-release_latest_7.4+ubuntu${{VERSION_ID}}_all.deb"
curl -fsSL --proto '=https' --tlsv1.2 \
  --retry 3 --retry-delay 2 --retry-max-time 60 \
  --connect-timeout 10 --max-time 60 --max-filesize 10485760 \
  -o /tmp/zabbix-release.deb "${{ZABBIX_RELEASE_DEB}}"
dpkg -i /tmp/zabbix-release.deb
apt-get update -qq
apt-get install -y zabbix-agent2
systemctl stop zabbix-agent2 2>/dev/null || true
cat > /etc/zabbix/zabbix_agent2.conf <<'ZABBIX_CONF'
LogFile=/var/log/zabbix/zabbix_agent2.log
LogFileSize=0
PidFile=/run/zabbix/zabbix_agent2.pid
ServerActive={server}
Hostname=${{HOSTNAME}}
Include=/etc/zabbix/zabbix_agent2.d/*.conf
PluginSocket=/run/zabbix/agent.plugin.sock
ZABBIX_CONF
systemctl enable --now zabbix-agent2
"""


def _normalize_nms_agent_backend_url(value: str) -> str:
    """Return a safe HTTPS agent backend URL suitable for rendered YAML."""

    from urllib.parse import urlsplit, urlunsplit

    backend_url = (value or "").strip() or "https://backend.nms.nmulti.cloud"
    try:
        parts = urlsplit(backend_url)
        # Accessing port also rejects malformed/out-of-range values.
        _ = parts.port
    except ValueError as exc:
        raise ValueError(f"Invalid nms_agent_backend_url: {backend_url!r}") from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("nms_agent_backend_url must be an HTTPS URL without credentials, query, or fragment")
    normalized = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    return normalized


def _nms_agent_allowed_units(template) -> list[str]:
    """Map durable template service markers to the agent's local allowlist."""

    if getattr(template, "provisions_service", "") == "akvorado":
        return ["akvorado.service"]
    return []


def _nms_agent_config(backend_url: str, allowed_units: list[str]) -> str:
    """Render the credential-free nms-agent configuration."""

    allowed = "[]" if not allowed_units else "\n" + "\n".join(f"    - {unit}" for unit in allowed_units)
    return f"""\
backend_url: {json.dumps(_normalize_nms_agent_backend_url(backend_url))}
identity_path: /etc/nms-agent/identity.json
token_path: /etc/nms-agent/token
signing_key_path: /etc/nms-agent/backend_signing.pub
log_level: info
otlp:
  enabled: true
  endpoint: {json.dumps(_normalize_nms_agent_backend_url(backend_url))}
  insecure: false
  headers: {{}}
zabbix:
  enabled: false
  manage_agent2: false
  server: zabbix.nmulti.cloud
  host_metadata: ""
intervals:
  poll_s: 15
  heartbeat_s: 60
  metrics_s: 30
  enroll_s: 30
rpc:
  enabled: true
  allowed_units: {allowed}
"""


def _nms_agent_systemd_unit() -> str:
    """Return the upstream-compatible nms-agent systemd service definition."""

    return """\
[Unit]
Description=NMS Agent - telemetry and RPC agent for the NMS platform
Documentation=https://git.nmulti.cloud/N-MultiCloud/nms-agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/nms-agent run --config /etc/nms-agent/config.yaml
Restart=on-failure
RestartSec=5
User=root
UMask=0077
NoNewPrivileges=false
ProtectHome=true

[Install]
WantedBy=multi-user.target
"""


def _nms_agent_bootstrap() -> str:
    """Build and install nms-agent from its pinned source commit.

    nms-agent currently publishes neither release binaries nor repository
    packages. The build therefore fetches one exact public Git commit and uses
    a SHA256-verified Go toolchain to produce its documented static binary.
    """

    return f"""\
#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
readonly NMS_AGENT_COMMIT='{_NMS_AGENT_COMMIT}'
readonly GO_VERSION='{_NMS_AGENT_GO_VERSION}'
readonly GO_SHA256='{_NMS_AGENT_GO_LINUX_AMD64_SHA256}'
readonly NMS_AGENT_REPOSITORY='https://git.nmulti.cloud/N-MultiCloud/nms-agent.git'

test "$(dpkg --print-architecture)" = 'amd64'
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git

workdir="$(mktemp -d)"
trap 'rm -rf "${{workdir}}"' EXIT
curl --fail --silent --show-error --location --retry 3 \
  --output "${{workdir}}/go.tar.gz" \
  "https://go.dev/dl/go${{GO_VERSION}}.linux-amd64.tar.gz"
printf '%s  %s\n' "${{GO_SHA256}}" "${{workdir}}/go.tar.gz" | sha256sum --check --strict -
tar -C "${{workdir}}" -xzf "${{workdir}}/go.tar.gz"

git init --quiet "${{workdir}}/nms-agent"
git -C "${{workdir}}/nms-agent" remote add origin "${{NMS_AGENT_REPOSITORY}}"
git -C "${{workdir}}/nms-agent" fetch --quiet --depth 1 origin "${{NMS_AGENT_COMMIT}}"
git -C "${{workdir}}/nms-agent" checkout --quiet --detach FETCH_HEAD
test "$(git -C "${{workdir}}/nms-agent" rev-parse HEAD)" = "${{NMS_AGENT_COMMIT}}"

cd "${{workdir}}/nms-agent"
"${{workdir}}/go/bin/go" mod download
ldflags=(
  '-s'
  '-w'
  '-X'
  "git.nmulti.cloud/N-MultiCloud/nms-agent/internal/version.Version=${{NMS_AGENT_COMMIT}}"
  '-X'
  "git.nmulti.cloud/N-MultiCloud/nms-agent/internal/version.Commit=${{NMS_AGENT_COMMIT}}"
)
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "${{workdir}}/go/bin/go" build \
  -trimpath \
  -ldflags "${{ldflags[*]}}" \
  -o "${{workdir}}/nms-agent-bin" ./cmd/nms-agent
install -o root -g root -m 0755 "${{workdir}}/nms-agent-bin" /usr/bin/nms-agent
/usr/bin/nms-agent version | grep -F "${{NMS_AGENT_COMMIT}}"

install -d -o root -g root -m 0700 /etc/nms-agent
systemctl daemon-reload
systemctl enable --now nms-agent.service
"""


def _inject_monitoring_agents(user_data_yaml: str, template) -> str:
    """Inject QEMU Guest Agent, Zabbix Agent 2, and/or nms-agent into cloud-config.

    Deduplication rules:
    - QEMU Guest Agent: skip package add if 'qemu-guest-agent' already in packages list;
      always add the systemctl enable runcmd entry if not already present.
    - Zabbix Agent 2: skip all injection if 'zabbix-agent2' appears anywhere in the YAML
      (handles templates that already manage Zabbix themselves, e.g. the Zabbix server seed).
    - nms-agent: disabled by default; skip injection only when all three managed files and
      the exact bootstrap command are already present. Partial state is completed.
    """
    import yaml  # stdlib-adjacent; always available in NetBox's Django env via PyYAML

    if not user_data_yaml or not user_data_yaml.strip():
        return user_data_yaml

    # Preserve the #cloud-config header line (must remain the very first line).
    lines = user_data_yaml.splitlines(keepends=True)
    header = lines[0].rstrip("\n") if lines and lines[0].startswith("#") else "#cloud-config"
    body = "".join(lines[1:]) if lines and lines[0].startswith("#") else user_data_yaml

    config = yaml.safe_load(body) or {}
    pkgs = list(config.get("packages", []))
    runcmds = list(config.get("runcmd", []))
    write_files = list(config.get("write_files", []))

    # --- QEMU Guest Agent ---
    if getattr(template, "install_qemu_guest_agent", True):
        if "qemu-guest-agent" not in pkgs:
            pkgs.append("qemu-guest-agent")
        # Add enable/start command if not already referenced in any runcmd entry.
        if not any("qemu-guest-agent" in str(r) for r in runcmds):
            runcmds.insert(0, ["bash", "-c", "systemctl enable --now qemu-guest-agent || true"])

    # --- Zabbix Agent 2 ---
    # Whole-YAML dedup: if the existing content already handles zabbix-agent2
    # (e.g. the Zabbix server template), skip injection to avoid a double-install.
    if getattr(template, "install_zabbix_agent2", True) and "zabbix-agent2" not in user_data_yaml:
        zabbix_server = (getattr(template, "zabbix_server", "") or "").strip() or "zabbix.nmulti.cloud"
        script_path = "/opt/nmulticloud-zabbix-agent2-bootstrap.sh"
        if not any((isinstance(f, dict) and f.get("path") == script_path) for f in write_files):
            write_files.append(
                {
                    "path": script_path,
                    "permissions": "0755",
                    "owner": "root:root",
                    "content": _zabbix_agent2_bootstrap(zabbix_server),
                }
            )
        if not any(script_path in str(r) for r in runcmds):
            runcmds.append(["bash", script_path])

    # --- NMS Agent ---
    nms_agent_enabled = getattr(template, "install_nms_agent", False)
    injection_complete = False
    if nms_agent_enabled:
        bootstrap_path = "/opt/nmulticloud-nms-agent-bootstrap.sh"
        expected_paths = {
            "/etc/nms-agent/config.yaml",
            "/etc/systemd/system/nms-agent.service",
            bootstrap_path,
        }
        existing_paths = {f.get("path") for f in write_files if isinstance(f, dict) and f.get("path")}
        bootstrap_command = ["bash", bootstrap_path]
        injection_complete = expected_paths.issubset(existing_paths) and (bootstrap_command in runcmds)

    if nms_agent_enabled and not injection_complete:
        backend_url = getattr(template, "nms_agent_backend_url", "")
        allowed_units = _nms_agent_allowed_units(template)
        nms_files = (
            ("/etc/nms-agent/config.yaml", "0600", _nms_agent_config(backend_url, allowed_units)),
            ("/etc/systemd/system/nms-agent.service", "0644", _nms_agent_systemd_unit()),
            (bootstrap_path, "0750", _nms_agent_bootstrap()),
        )
        for path, permissions, content in nms_files:
            if path not in existing_paths:
                write_files.append(
                    {
                        "path": path,
                        "permissions": permissions,
                        "owner": "root:root",
                        "content": content,
                    }
                )
        if bootstrap_command not in runcmds:
            runcmds.append(bootstrap_command)

    # --- Password SSH auth ---
    # Every cloud-init template must support username+password SSH (key-based
    # stays the default). This only *permits* password auth in the guest sshd;
    # the per-VM password itself is supplied at clone time via Proxmox
    # cloud-init (cipassword) and is never baked into the image. Check the
    # parsed config (not the raw string) so an explicit `ssh_pwauth:` key wins
    # while an unrelated comment mentioning it does not skip injection.
    if "ssh_pwauth" not in config:
        config["ssh_pwauth"] = True

    if pkgs:
        config["packages"] = pkgs
    if runcmds:
        config["runcmd"] = runcmds
    if write_files:
        config["write_files"] = write_files

    serialized = yaml.safe_dump(config, default_flow_style=False, allow_unicode=True)
    return header + "\n" + serialized


_EXPLORER_SERVICE_MARKER = "influxdb3-explorer"
_EXPLORER_CONFIG_PATH = "/etc/influxdb3-explorer/config.json"
_EXPLORER_PLACEHOLDER_SECRET_REF = "nms-secret:<opaque-id>"
_EXPLORER_ALLOWED_URLS = frozenset({"http://127.0.0.1:8080/"})
_EXPLORER_CREDENTIAL_KEY_PARTS = (
    "password",
    "passphrase",
    "secret",
    "token",
    "authorization",
    "api_key",
    "access_key",
    "private_key",
    "credential",
)
_EXPLORER_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?:^\s*|[,{]\s*|-\s+)[\"']?[A-Za-z0-9_.-]*"
    r"(?:token|password|passphrase|secret|authorization|api[-_]?key|"
    r"access[-_]?key|private[-_]?key|credential)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[^\s]+",
    re.IGNORECASE | re.MULTILINE,
)
_EXPLORER_CORE_SETTING_RE = re.compile(
    r"(?:^\s*|[,{]\s*|-\s+)(?:export\s+)?[\"']?[A-Za-z0-9_.-]*"
    r"(?:(?:influxdb|core)[A-Za-z0-9_.-]*(?:url|host|endpoint)|"
    r"(?:url|host|endpoint)[A-Za-z0-9_.-]*(?:influxdb|core))"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]",
    re.IGNORECASE | re.MULTILINE,
)
_EXPLORER_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)
_EXPLORER_AUTHORIZATION_RE = re.compile(r"\b(?:authorization|bearer)\s*[:=]\s*\S+", re.IGNORECASE)
_EXPLORER_USERINFO_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)
_EXPLORER_SECRET_REF_RE = re.compile(r"nms-secret:[^\s\"']+", re.IGNORECASE)
_EXPLORER_HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _explorer_payload_error(reason: str) -> RuntimeError:
    return RuntimeError(
        "Refusing InfluxDB 3 Explorer bake: the fully injected cloud-config "
        f"violates the credential-free boundary ({reason}). Remove Core connection "
        "and credential material; provision config.json only after cloning."
    )


def _validate_influxdb3_explorer_payload(user_data_yaml: str) -> None:
    """Fail closed if the final Explorer cloud-config contains connection secrets."""

    import yaml

    try:
        config = yaml.safe_load(user_data_yaml)
    except yaml.YAMLError as exc:
        raise _explorer_payload_error("the final payload is not valid YAML") from exc
    if not isinstance(config, dict):
        raise _explorer_payload_error("the final payload is not a YAML mapping")

    write_files = config.get("write_files", [])
    if not isinstance(write_files, list):
        raise _explorer_payload_error("write_files is not a list")
    for entry in write_files:
        if not isinstance(entry, dict):
            raise _explorer_payload_error("write_files contains a non-mapping entry")
        if entry.get("path") == _EXPLORER_CONFIG_PATH:
            raise _explorer_payload_error("the golden image writes Explorer config.json")
        if entry.get("encoding"):
            raise _explorer_payload_error("encoded write_files content cannot be inspected safely")

    pending = [config]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized_key = str(key).lower().replace("-", "_")
                if any(part in normalized_key for part in _EXPLORER_CREDENTIAL_KEY_PARTS):
                    raise _explorer_payload_error("a credential-bearing YAML key is present")
                pending.append(nested)
            continue
        if isinstance(item, list):
            pending.extend(item)
            continue
        if not isinstance(item, str):
            continue

        for match in _EXPLORER_SECRET_REF_RE.finditer(item):
            secret_ref = match.group(0).rstrip(".,;)]}")
            if secret_ref != _EXPLORER_PLACEHOLDER_SECRET_REF:
                raise _explorer_payload_error("a non-placeholder nms-secret reference is present")
        credential_scan_value = item.replace(_EXPLORER_PLACEHOLDER_SECRET_REF, "")
        if _EXPLORER_PRIVATE_KEY_RE.search(item):
            raise _explorer_payload_error("private key material is present")
        if _EXPLORER_CREDENTIAL_ASSIGNMENT_RE.search(
            credential_scan_value
        ) or _EXPLORER_AUTHORIZATION_RE.search(credential_scan_value):
            raise _explorer_payload_error("a credential-bearing value is present")
        if _EXPLORER_USERINFO_URL_RE.search(item):
            raise _explorer_payload_error("URL userinfo is present")
        if "8181" in item or _EXPLORER_CORE_SETTING_RE.search(item):
            raise _explorer_payload_error("an InfluxDB Core endpoint setting is present")
        for match in _EXPLORER_HTTP_URL_RE.finditer(item):
            url = match.group(0).rstrip(".,;)]}")
            if url not in _EXPLORER_ALLOWED_URLS:
                raise _explorer_payload_error("an unapproved connection URL is present")


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

        # Endpoint-agnostic profiles carry their cluster and node selectors in
        # variable_overrides.  Legacy templates retain affinity-based fallback.
        requested_node = str((build.variable_overrides or {}).get("target_node") or "").strip()
        endpoint, node = select_build_node(
            template,
            skip_affinity_check=bool(requested_node and _resolve_endpoint_id(build.variable_overrides)),
        )
        if requested_node:
            node = requested_node
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
        fileserver_package_read_token = settings_row.get_fileserver_package_read_token()
        api_url = (settings_row.proxbox_api_url or "").strip()
        installer = template.installer_config
        storage = _resolve_storage(template, build.variable_overrides)
        # proxbox-api rejects an empty target_node (min_length=1); send None when unset.
        target_node = _resolve_target_node(template, node, build.variable_overrides)
        image_url, image_sha256 = _resolve_cloud_image_source(template, build.variable_overrides)
        safe_image_url = redact_base_image_url(image_url)
        ssh_host = _resolve_ssh_host(template, build.variable_overrides)
        endpoint_id = _resolve_endpoint_id(build.variable_overrides)
        template_vmid = _resolve_template_vmid(template, build.variable_overrides)
        installer_name = str(getattr(installer, "name", "") or "unnamed")
        installer_version = str(getattr(installer, "version", "") or "unknown")

        log_lines = [
            f"[INFO] Cloud-init template image build for '{template.name}'",
            f"[INFO] Delegating real Proxmox bake to proxbox-api: {api_url or 'UNSET'}",
            f"[INFO] Installer config: {installer_name} v{installer_version} ({installer.installer_type})",
            f"[INFO] Base image: {safe_image_url}",
            f"[INFO] Proxmox SSH host: {ssh_host or 'derived from endpoint'} | storage: {storage}",
            f"[INFO] Destination template VMID: {template_vmid}",
            "[INFO] proxbox-api endpoint_id: "
            + (
                str(endpoint_id)
                if endpoint_id is not None
                else "UNSET (required by proxbox-api when execute=true — pass variable_overrides['endpoint_id'])"
            ),
        ]

        if not api_url:
            build.log = "\n".join(log_lines)
            build.save(update_fields=["log"])
            raise RuntimeError(
                "PackerPluginSettings.proxbox_api_url is not configured; cannot bake a "
                "cloud_config template image via proxbox-api."
            )

        user_data_yaml = _inject_monitoring_agents(installer.content, template)
        user_data_yaml = render_fileserver_package_index(
            user_data_yaml,
            settings_row=settings_row,
            template_name=template.name,
            is_fileserver_golden_template=template.is_fileserver_golden_template,
        )
        zabbix_status = "disabled"
        if template.install_zabbix_agent2:
            zabbix_status = f"enabled (server={template.zabbix_server or 'zabbix.nmulti.cloud'})"
        log_lines += [
            f"[INFO] QEMU Guest Agent injection: {'enabled' if template.install_qemu_guest_agent else 'disabled'}",
            f"[INFO] Zabbix Agent 2 injection: {zabbix_status}",
            "[INFO] NMS Agent injection: "
            + (
                f"enabled (backend={_normalize_nms_agent_backend_url(template.nms_agent_backend_url)})"
                if getattr(template, "install_nms_agent", False)
                else "disabled"
            ),
            "[INFO] proxbox-api signed handshake: plan -> preflight -> execute",
        ]

        if getattr(template, "provisions_service", "") == _EXPLORER_SERVICE_MARKER:
            try:
                _validate_influxdb3_explorer_payload(user_data_yaml)
            except RuntimeError as exc:
                log_lines.append(f"[ERROR] {exc}")
                build.log = "\n".join(log_lines)
                build.save(update_fields=["log"])
                raise

        try:
            response = call_proxbox_build(
                proxbox_api_url=api_url,
                proxbox_api_key=settings_row.get_proxbox_api_key(),
                name=template.name,
                vmid=template_vmid,
                target_node=target_node,
                image_url=image_url,
                image_sha256=image_sha256,
                user_data_yaml=user_data_yaml,
                image_storage=storage,
                vm_storage=storage,
                storage=storage,
                snippets_storage=storage,
                ssh_host=ssh_host,
                endpoint_id=endpoint_id,
                timeout=int(timeout) + 300,
            )
        except ProxboxApiError as exc:
            safe_error = redact_fileserver_package_token(str(exc), fileserver_package_read_token)
            safe_error = safe_error.replace(image_url, safe_image_url)
            log_lines.append(f"[ERROR] {safe_error}")
            build.log = "\n".join(log_lines)
            build.save(update_fields=["log"])
            raise sanitized_fileserver_package_error(exc, fileserver_package_read_token) from None

        status = str(response.get("status", "")).lower()
        result_vmid = response.get("vmid") or response.get("template_vmid")
        log_lines.append(f"[INFO] proxbox-api status: {status or 'unknown'} (vmid={result_vmid})")
        for key in ("build_script", "stdout", "stderr"):
            value = response.get(key)
            if value:
                safe_value = redact_fileserver_package_token(str(value), fileserver_package_read_token)
                safe_value = safe_value.replace(image_url, safe_image_url)
                log_lines.append(f"[{key.upper()}]\n{safe_value}")

        build.finished_at = timezone.now()
        if status in {"created", "completed", "already_exists"}:
            build.status = "success"
            build.exit_code = 0
            build.base_image_url_at_build = safe_image_url
            build.base_image_sha256_at_build = image_sha256
            if result_vmid:
                build.result_template_id = int(result_vmid)
        else:
            build.status = "failed"
            build.exit_code = response.get("returncode") or 1
            PackerTemplate.objects.filter(pk=template.pk).update(build_status="failed")

        build.log = "\n".join(log_lines)
        build_update_fields = ["status", "finished_at", "exit_code", "result_template_id", "log"]
        if build.status == "success":
            from django.db import transaction

            build_update_fields += ["base_image_url_at_build", "base_image_sha256_at_build"]
            with transaction.atomic():
                current_template = PackerTemplate.objects.select_for_update().get(pk=template.pk)
                source_drifted = pin_differs_from_built_source(
                    desired_url=current_template.base_image_url,
                    desired_sha256=current_template.base_image_sha256,
                    built_url=safe_image_url,
                    built_sha256=image_sha256,
                )
                update = {
                    "build_status": "stale" if source_drifted else "ready",
                    "built_at": build.finished_at,
                    "base_image_url_at_build": safe_image_url,
                    "base_image_sha256_at_build": image_sha256,
                }
                if installer:
                    update["installer_config_checksum_at_build"] = installer.checksum
                PackerTemplate.objects.filter(pk=current_template.pk).update(**update)
                build.save(update_fields=build_update_fields)
        else:
            build.save(update_fields=build_update_fields)

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

        timeout_seconds = int(timeout)
        timed_out = threading.Event()

        def kill_on_timeout():
            if proc.poll() is not None:
                return
            try:
                proc.kill()
            except ProcessLookupError:
                return
            timed_out.set()

        timeout_timer = threading.Timer(timeout_seconds, kill_on_timeout)
        timeout_timer.daemon = True
        timeout_timer.start()

        try:
            for line_count, line in enumerate(proc.stdout, start=1):
                log_lines.append(line.rstrip())
                # Partial save every 50 lines so logs appear incrementally
                if line_count % 50 == 0:
                    build.log = "\n".join(log_lines)
                    build.save(update_fields=["log"])

            proc.wait()
        finally:
            timeout_timer.cancel()

        if timed_out.is_set():
            log_lines.append(f"[ERROR] Timeout exceeded ({timeout_seconds}s) during {phase}")
            build.log = "\n".join(log_lines)
            build.save(update_fields=["log"])
            return 124

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
            rebuild_ids = []

            # Age is only one source of staleness. Installer checksum and base-image
            # pin drift must still be evaluated when max_age_days is unset.
            for template in PackerTemplate.objects.exclude(build_status__in=("building",)):
                checked += 1
                if not template.is_stale:
                    continue

                stale += 1
                PackerTemplate.objects.filter(pk=template.pk).update(build_status="stale")

                if not template.auto_rebuild:
                    continue

                if PackerBuild.objects.filter(template=template, status="running").exists():
                    continue

                build = (
                    PackerBuild.objects.filter(template=template, status="queued")
                    .order_by("queued_at")
                    .first()
                )
                if build is None:
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
                else:
                    logger.warning(
                        "Recovering queued rebuild #%s for stale template '%s'",
                        build.pk,
                        template.name,
                    )
                rebuild_ids.append(build.pk)

            return checked, stale, rebuild_ids

        checked = 0
        stale = 0
        rebuild_ids = []
        if branch is not None:
            with activate_branch_context(branch):
                checked, stale, rebuild_ids = _run_staleness(PackerBuild, PackerTemplate)
            merged, msg = merge_branch(
                branch=branch,
                user=None,
                on_conflict=branch_config["on_conflict"],
            )
            if merged:
                logger.info("Staleness check branch merged: %s", msg)
            else:
                logger.warning("Staleness check branch merge failed: %s", msg)
                rebuild_ids = []
        else:
            checked, stale, rebuild_ids = _run_staleness(PackerBuild, PackerTemplate)

        dispatched = 0
        for build_id in rebuild_ids:
            build = PackerBuild.objects.select_related("template").get(pk=build_id)
            PackerTemplate.objects.filter(pk=build.template_id).update(build_status="building")
            try:
                dispatch_build(build)
            except Exception:
                logger.exception("Auto-rebuild dispatch failed for build #%s", build_id)
                continue
            dispatched += 1

        logger.info(
            "Staleness check complete: %d templates checked, %d stale, %d rebuilds dispatched",
            checked,
            stale,
            dispatched,
        )


def dispatch_build(build):
    """Enqueue a PackerBuildJob (RQ background) for an existing PackerBuild.

    Marks the build failed and re-raises on enqueue failure so callers can decide
    how to surface it. This is the single dispatch point used by both the REST
    API and the HTML build action, fixing the gap where creating a PackerBuild
    never started a job.
    """
    try:
        # No `instance=`: PackerBuild is not a jobs-assignable object type in NetBox
        # ("Jobs cannot be assigned to this object type"); the job links via build_id.
        PackerBuildJob.enqueue(build_id=build.pk)
    except Exception as exc:
        _mark_enqueue_failed(build, exc)
        logger.exception("Failed to enqueue PackerBuildJob for build #%s", build.pk)
        raise


def _mark_enqueue_failed(build, exc):
    """Persist a failed build state when the RQ enqueue operation itself fails."""
    build.status = "failed"
    build.finished_at = timezone.now()
    error_line = f"[ERROR] Failed to enqueue PackerBuildJob: {exc}"
    build.log = f"{build.log or ''}\n{error_line}".strip()
    build.save(update_fields=["status", "finished_at", "log"])

    template_id = getattr(build, "template_id", None)
    if not template_id:
        template = getattr(build, "template", None)
        template_id = getattr(template, "pk", None)
    if not template_id:
        return

    from .models import PackerBuild, PackerTemplate

    has_other_active_build = (
        PackerBuild.objects.filter(
            template_id=template_id,
            status__in=("queued", "running"),
        )
        .exclude(pk=build.pk)
        .exists()
    )
    if not has_other_active_build:
        PackerTemplate.objects.filter(pk=template_id).update(build_status="failed")
