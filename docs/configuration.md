# Configuration

## Plugin registration

Add `netbox_packer` to your `PLUGINS` list and configure it in
`PLUGINS_CONFIG` inside `netbox/configuration.py`:

```python
PLUGINS = [
    # ... other plugins
    "netbox_packer",
]

PLUGINS_CONFIG = {
    "netbox_packer": {
        # No required keys at startup.
        # Runtime settings are stored in the PackerPluginSettings singleton.
    },
}
```

## PackerPluginSettings

After running migrations, the singleton settings row exists in the database
(`PackerPluginSettings.get_solo()`), but **there is currently no NetBox UI page
and no REST API endpoint for it** — there is no navigation menu entry, no
registered model view, and the plugin's API router only registers
`packer-templates`, `build-jobs`, `installer-configs`, and `build-targets` (not
`plugin-settings`). The only supported way to read or write these fields today
is the Django/NetBox Python shell (`manage.py nbshell` or `manage.py shell`);
see "Storing the key" below.

| Setting | Model field | Description |
| --- | --- | --- |
| Proxbox API URL | `proxbox_api_url` | One exact HTTP(S) origin for the proxbox-api backend (e.g. `https://proxbox-api.internal.example:8000`). HTTPS is required except for literal loopback development endpoints. Required for `cloud_config` installer-type builds. |
| Proxbox API key | `proxbox_api_key_encrypted` | Set only via `set_proxbox_api_key()` / read via `get_proxbox_api_key()` — never stored or read as plaintext. See key management below. |
| File Server package-read user | `fileserver_package_read_user` | Plaintext username for the dedicated non-human Gitea package reader. |
| File Server package-read token | `fileserver_package_read_token_encrypted` | Set only via `set_fileserver_package_read_token()` / read via `get_fileserver_package_read_token()` — never stored or read as plaintext. |
| Enable Proxbox writes | `proxbox_writes_enabled` | Fail-closed gate for template builds and VM provisioning. Migration `0024` creates it as `False`; enable it only for the controlled canary and normal operation after validation. |
| Enable branching | `branching_enabled` | When `True`, staleness-check jobs run inside a netbox-branching branch. |
| Branch name prefix | `branch_name_prefix` | Prefix for auto-created branch names (default: `packer-stale`). |
| Branch conflict behavior | `branch_on_conflict` | `fail` (leave branch open) or `acknowledge` (merge anyway). |

## Proxbox API key management

The proxbox-api key is **not** stored in plain text. It is encrypted with a
Fernet cipher derived from `settings.SECRET_KEY` (SHA-256 → base64url). There
is **no dependency on `netbox-nms`** for this encryption.

### Storing the key

**Via the Python shell.** This is currently the only supported way to set or
rotate the key (there is no UI form or REST endpoint for `PackerPluginSettings`
yet):

```python
from netbox_packer.models import PackerPluginSettings

settings_row = PackerPluginSettings.get_solo()
settings_row.set_proxbox_api_key("your-proxbox-api-key-here")
settings_row.save()
```

`proxbox_api_url` can be set the same way (`settings_row.proxbox_api_url = "..."`)
before calling `.save()`.

### Verifying the key

```python
from netbox_packer.models import PackerPluginSettings

settings_row = PackerPluginSettings.get_solo()
key = settings_row.get_proxbox_api_key()
print("Key configured:", bool(key))
```

## File Server package-index credential management

The File Server golden-image bake reads its package-index credential from the
same singleton settings row, not from the NetBox or RQ service environment.
The username is not secret; the token uses the same Django `SECRET_KEY`-derived
Fernet cipher as the proxbox-api key. Set it from the Django/NetBox shell:

```python
from netbox_packer.models import PackerPluginSettings

settings_row = PackerPluginSettings.get_solo()
settings_row.fileserver_package_read_user = "nms-pkg-reader"
settings_row.set_fileserver_package_read_token("<gitea-package-read-token>")
settings_row.save()
```

Use a dedicated non-human Gitea identity and grant package-Read permission only.
Never use a personal token or `PACKAGE_WRITE_TOKEN`. When the token changes,
rotate it through the setter and rebake File Server VMID `9300`; existing images
and clones retain the credential already baked into root-only
`/etc/nms-fileserver-agent/pip.conf`.

## proxbox-api prerequisites

The `cloud_config` bake path requires:

- **`proxbox-api >= 0.0.21,<0.0.22`** — the compatible API line provides the
  secret-safe build-response v2 contract and signed read-only preflight v1
  contract. Earlier releases execute through caller-supplied legacy SSH
  authority and are intentionally rejected by this client.
- **`PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION=true`** — set in the proxbox-api
  environment. Cloud image execution is disabled by default.
- **`PROXBOX_SSH_KEY_DIR`** — directory on the proxbox-api host containing the
  SSH private key that trusts the target Proxmox host.
- **`allow_writes=True`** on the target `ProxmoxEndpoint` row in netbox-proxbox.
- The selected Proxmox storage pool must have the `snippets` and `images`
  content types enabled. `import` is sent as the download-url request value;
  it is not a Proxmox storage content type to configure.

For detailed host bootstrap steps (key provisioning, storage content types), see
the `nmulticloud-context` deploy documentation at
`deploy/docs/proxbox-api-cloud-image-bake.md`.

## Proxbox API origin and redirect policy

`proxbox_api_url` is a credential boundary, not a general URL prefix. Configure
one exact HTTP(S) origin consisting only of a scheme, hostname or IP address,
and optional port. A single root trailing slash is normalized. The client
rejects userinfo (embedded usernames or passwords), non-root paths, query
strings, fragments, whitespace/control characters, ambiguous encoded
authorities, and schemes other than `http` or `https` before any network access.

HTTPS is required for every non-loopback origin. Plain HTTP is accepted only
when the hostname is a literal IPv4 or IPv6 loopback address such as
`127.0.0.1` or `::1`; `localhost`, private RFC 1918 addresses, and other
hostnames do not qualify. This keeps `X-Proxbox-API-Key` off cleartext networks.

The credential-bearing client does not follow HTTP redirects. This includes
same-origin redirects. A 3xx response fails closed and asks the operator to
configure the canonical backend origin. This prevents `X-Proxbox-API-Key` from
being copied to a redirect target. Correct a legacy URL in
`PackerPluginSettings`; do not work around the failure by adding credentials to
the URL or weakening TLS verification.

The client does not use environment-configured HTTP proxies (`HTTP_PROXY`,
`HTTPS_PROXY`, or `ALL_PROXY`). A proxy is another credential recipient, not
the configured proxbox-api origin. If the NetBox host requires an egress proxy,
configure a direct route for proxbox-api instead of forwarding this
credential-bearing request through the proxy.

The key must be a nonempty, header-safe ASCII value. Error messages use fixed
diagnostics and never include backend response bodies or transport exception
text. Successful bodies are limited to 1 MiB, must arrive within a 30-second
post-header deadline, and must decode to a JSON object. These bounds protect
the synchronous web and worker processes from reflected secrets and unbounded
dependency responses.

Successful responses are then checked against the endpoint-specific contract.
Executable template builds require a positive `endpoint_id` and validated
`target_node` before any request. The client renders an HTTP 201 non-executing
v2 plan, submits its opaque recipe digest to HTTP 200 read-only preflight, and
sends the returned signed plan token only in the final HTTP 201 execution. All
three responses must retain the exact endpoint, node, and VMID binding. Only a
verified `completed` result is successful; `failed` and `recovery_required`
remain explicit terminal failures, with the latter preserving its validated
operation ID for operator recovery. VM provisioning requires HTTP 200 and accepts only the requested
VMID and the expected `started` or `stopped` status. Other 2xx responses are not
treated as terminal success. The client returns only this allowlisted metadata.
Backend build scripts, command lists, generated user data, stdout, stderr, and
other untrusted fields are discarded before durable logging because they can
contain credentials or signed artifact URLs.

`image_url` is not supported at any depth in
`PackerBuild.variable_overrides`. Accepting it would durably store presigned
query credentials in NetBox, its API, and backups before the worker ran. Model,
form, and API validation recursively reject the key; REST/model read helpers
recursively redact it defensively, GraphQL excludes both raw persistence
fields, and PackerBuild change-log serialization omits the raw fields so NetBox
`ObjectChange` snapshots cannot become an alternate disclosure path. Migration
`0024` removes every nested occurrence from legacy build rows and removes raw
`variable_overrides`/`log` fields from their existing ObjectChange snapshots,
without logging or restoring values. The same
migration replaces recognizable legacy proxbox build logs wholesale because
the old format embedded base-image URLs, backend scripts, stdout, and stderr;
REST and HTML reads apply the same log redaction defensively. Cloud image URLs
must be derived from the server-side catalog. Legacy `ssh_host` values may
remain in historical non-secret data, but the executable client never forwards
caller-selected SSH hosts or identity files.

## Legacy configuration preflight and migration

Use the following staged procedure for every existing installation. Keep job
dispatch quiesced until the post-upgrade validation passes. The automatic
deployment workflow first requires the local helper to advertise the read-only
`netbox-service-guard-v1` capability. Legacy helpers are rejected before any
mutation. A compatible helper owns the complete web/RQ stop, mask, package and
schema mutation, fresh start, and health sequence under its shared host-wide
NetBox deployment lock; it must handle initially inactive and no-runtime-change
deployments and leave both services stopped on failure. The workflow does not
manipulate services outside that shared lock. Migration `0024`
adds a second, in-process safety boundary, `proxbox_writes_enabled=False`,
before any worker or synchronous view can reach proxbox-api.

### Stage 1: inventory, backup, and pre-upgrade check

1. Stop new Packer build dispatch and let running builds reach a terminal
   state. For an automatic deployment, confirm the environment has the
   coordinated `netbox-service-guard-v1` helper; without it, the workflow exits
   before mutation. The compatible helper accepts either active or inactive
   services and owns their entire lifecycle under its shared deploy lock. For a
   manual maintenance operation, stop both services before changing the package
   and keep them stopped on every failure.
   Record the prior plugin version and the existing non-secret URL; do not
   record or print the decrypted key.
2. Take the normal NetBox database backup and verify the restore procedure.
   Retain the prior plugin wheel/package as the rollback artifact.
3. On the still-running old package, run `python manage.py shell` and execute
   the script below. It intentionally uses only Python's standard library and
   the long-standing `PackerPluginSettings` methods, so it also works on plugin
   versions that do not yet provide `normalize_proxbox_api_base_url()`.

The script prints no URL or key material and exits nonzero when the stored
configuration would be rejected after the upgrade:

```python
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from netbox_packer.models import PackerPluginSettings

# Leave False for a read-only preflight. If the final message says a canonical
# update is required, rerun this same old-version-compatible script with True.
APPLY_CANONICAL_UPDATE = False

settings_row = PackerPluginSettings.get_solo()
stored_url = settings_row.proxbox_api_url

if (
    not isinstance(stored_url, str)
    or not stored_url
    or stored_url != stored_url.strip()
    or "\\" in stored_url
    or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in stored_url)
):
    raise SystemExit("BLOCKED: proxbox-api URL is empty or contains unsafe characters")

try:
    parsed = urlsplit(stored_url)
    hostname = parsed.hostname
    port = parsed.port
except ValueError:
    raise SystemExit("BLOCKED: proxbox-api hostname or port is malformed") from None

if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
    raise SystemExit("BLOCKED: proxbox-api URL must be one absolute HTTP(S) origin")
if (
    not parsed.netloc.isascii()
    or "%" in parsed.netloc
    or parsed.username is not None
    or parsed.password is not None
    or parsed.netloc.endswith(":")
    or port == 0
    or parsed.path not in {"", "/"}
    or "?" in stored_url
    or "#" in stored_url
):
    raise SystemExit("BLOCKED: proxbox-api URL contains an unsafe authority, path, query, or fragment")
if parsed.scheme == "http":
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise SystemExit("BLOCKED: proxbox-api URL requires HTTPS outside literal loopback")

canonical_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
try:
    api_key = settings_row.get_proxbox_api_key()
except Exception:
    raise SystemExit("BLOCKED: proxbox-api key cannot be decrypted") from None
if (
    not isinstance(api_key, str)
    or not api_key
    or len(api_key) > 4096
    or api_key != api_key.strip()
    or not api_key.isascii()
    or any(ord(character) < 33 or ord(character) == 127 for character in api_key)
):
    raise SystemExit("BLOCKED: proxbox-api key is missing, unreadable, or not header-safe")
del api_key

if canonical_url != stored_url:
    if not APPLY_CANONICAL_UPDATE:
        raise SystemExit(
            "BLOCKED: canonical URL update required; review the stored value, then rerun "
            "this script with APPLY_CANONICAL_UPDATE=True"
        )
    settings_row.proxbox_api_url = canonical_url
    settings_row.save(update_fields=["proxbox_api_url"])
    print("UPDATED: stored proxbox-api URL normalized without printing it")

print("READY: legacy configuration passes the new origin and key policy")
```

Do not substitute a placeholder URL. The optional update persists only the
canonical form computed from the installation's actual stored value. Do not
rotate or print the key unless the credential itself also needs replacement.

### Stage 2: upgrade with writes held

1. Confirm there are no queued or running `PackerBuild` rows, then promote the
   package while normal build/create permissions remain withdrawn. Require a
   green deploy workflow: `netbox-staging.service` plus
   `netbox-staging-rq.service`, or `netbox.service` plus `netbox-rq.service`,
   are passed to a helper that has advertised `netbox-service-guard-v1`.
   Under the shared host-wide lock, that helper stops both before mutation,
   keeps RQ masked, starts and freshness-checks web before RQ, and leaves both
   stopped on any failure. The workflow rejects a legacy helper before
   mutation and trusts only the helper's in-lock freshness/health result; it
   does not race a queued deployment with post-lock service inspection. Do not enable the gate or
   begin a canary after a failed or bypassed freshness check, and do not
   interpret a running worker as permission to write.
2. Run `python manage.py migrate` and `python manage.py check` if the deployment
   helper did not already run them. Migration `0024` scrubs legacy `image_url`
   overrides, unsafe legacy proxbox build logs, and raw PackerBuild fields in
   NetBox ObjectChange snapshots, then creates
   `proxbox_writes_enabled=False`, so restarted workers, API builds, and
   synchronous VM provisioning fail closed before a proxbox-api request. The
   selector-less HTML cloud-build action and automatic cloud rebuilds are
   disabled independently; only the authorized API/NMS action can supply the
   required endpoint and node binding.
3. Run the post-upgrade check below. It uses the installed shared validator,
   verifies the write hold, and still prints no URL or key material:

```python
from netbox_packer.models import PackerPluginSettings
from netbox_packer.proxbox_client import normalize_proxbox_api_base_url

settings_row = PackerPluginSettings.get_solo()
normalize_proxbox_api_base_url(settings_row.proxbox_api_url)
if settings_row.proxbox_writes_enabled:
    raise SystemExit("BLOCKED: Proxbox write gate unexpectedly enabled after migration")
api_key = settings_row.get_proxbox_api_key()
if not api_key:
    raise SystemExit("BLOCKED: proxbox-api key is not configured or cannot be decrypted")
del api_key
print("READY: upgraded client accepts the stored origin and encrypted key")
```

### Stage 3: controlled validation and resume

1. Run the package checks before accepting Proxbox writes:

   ```bash
   python -m compileall -q netbox_packer tests
   ruff check .
   ruff format --check netbox_packer tests/test_cloud_config_build_static.py \
     tests/test_plugin_structure_static.py tests/test_proxbox_client_security.py \
     tests/test_build_persistence_security.py
   ty check netbox_packer/proxbox_client.py
   pytest tests/ -v
   python manage.py migrate --plan
   python manage.py check
   ```
2. Keep normal user build/create permissions withdrawn. In the NetBox staging
   shell, enable the gate without displaying other settings:

   ```python
   from netbox_packer.models import PackerPluginSettings

   settings_row = PackerPluginSettings.get_solo()
   settings_row.proxbox_writes_enabled = True
   settings_row.save(update_fields=["proxbox_writes_enabled"])
   print("READY: Proxbox write gate enabled")
   ```

3. With an approved change window, queue exactly one controlled staging bake
   against a dedicated non-production target and verify the expected
   status/VMID. This is a real Proxmox mutation, not a read-only probe; perform
   it only through the normal NMS workflow and normal approval controls. The
   restarted worker pool is safe here because dispatch remains restricted and
   only the canary is queued. Confirm the NetBox build record, serialized API
   response, worker logs, and database `variable_overrides` contain no scripts,
   process output, cloud-init content, API keys, or signed URLs.
4. If the canary fails, set `proxbox_writes_enabled=False` before investigation.
   If it passes, restore normal build/create permissions and leave the gate
   enabled. Monitor failed-build rate and proxbox-api HTTP status counts through
   the normal operations surface. Repeat the same gated sequence for production.

### Rollback

Rollback is a full maintenance operation; never reverse the schema while web
or worker processes still import the new model.

1. Withdraw normal build/create permissions, set
   `proxbox_writes_enabled=False`, and wait for queued/running builds plus every
   in-flight synchronous provision/build request to reach a terminal response.
   Drain new HTTP traffic through the normal maintenance control.
2. Stop and runtime-mask both the environment's NetBox web unit and RQ unit.
   Staging uses `netbox-staging.service` and `netbox-staging-rq.service`.
   Production uses `netbox-rq.service` plus whichever web unit actually serves
   the environment (`netbox.service` or `netbox-production.service`). Confirm
   every selected unit is inactive before touching schema or package files.
3. While all selected units remain stopped and masked, keep the new package
   (and therefore migrations `0023`/`0024`) installed long enough to run
   `python manage.py migrate netbox_packer 0022`. Only after that reverse
   migration succeeds, restore the prior plugin package and recorded non-secret
   URL, then run `python manage.py migrate` and `python manage.py check` with
   the prior code. This ordering avoids both old-code/new-schema and
   new-code/old-schema live-process mismatches.
4. If any step fails, leave web and RQ stopped, restore the verified database
   backup when required, and repair under maintenance. After every command is
   green, unmask and start the web service first, verify health, then start the
   RQ service and verify its process identity before restoring traffic or
   permissions.

Migration `0023` changes field metadata only. Migration `0024` irreversibly
removes legacy `image_url` override keys, unsafe proxbox logs, and raw
PackerBuild fields from historical ObjectChange snapshots; rollback
intentionally does not restore credential-bearing URLs or backend output.
Re-run the old-version-compatible preflight before re-enabling workers and
normal permissions. Preserve failed build records as audit evidence. Do not
weaken TLS, enable redirects or proxies, or expose the key as a rollback
shortcut.

## Environment variables (proxbox-api side)

These are set on the proxbox-api service, not on the NetBox host:

| Variable | Required | Description |
| --- | --- | --- |
| `PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION` | Yes | Set to `true` to enable bake jobs |
| `PROXBOX_SSH_KEY_DIR` | Yes | Directory containing SSH private key(s) for Proxmox hosts |
| `PROXBOX_NETBOX_TIMEOUT` | No | Timeout for NetBox API calls, default `120s` |
| `PROXBOX_ENCRYPTION_KEY` | Yes | Fernet key for proxbox-api credential storage |

## Configuration validation

After saving `PackerPluginSettings`, verify that the required values are present
without printing the key. This is the same check used by the post-upgrade
preflight, shortened for routine validation:

```python
from netbox_packer.models import PackerPluginSettings
from netbox_packer.proxbox_client import normalize_proxbox_api_base_url

s = PackerPluginSettings.get_solo()
print("Canonical URL:", normalize_proxbox_api_base_url(s.proxbox_api_url))
print("Key set:", bool(s.get_proxbox_api_key()))
```

Do not write an ad-hoc `urllib` connectivity probe with the API-key header: a
default opener may follow redirects. Normal build/provision requests validate
the origin and apply the no-redirect policy in the shared plugin client.
