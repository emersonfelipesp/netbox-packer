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
        # Runtime settings are managed via PackerPluginSettings in the UI.
    },
}
```

## Proxbox API linkage

netbox-proxbox is the single source of truth for how to reach proxbox-api.
Cloud-init builds (and the create-instance modal) reuse its enabled
`FastAPIEndpoint` by default:

| Stored `proxbox_api_url` | Linkage used |
| --- | --- |
| blank | The base URL (hostname URL preferred, then the IP-address URL), API key, and TLS verification choice of the **single** enabled netbox-proxbox FastAPI endpoint. Zero or several enabled endpoints fail closed with an actionable message. |
| a URL that matches exactly one enabled netbox-proxbox FastAPI endpoint (scheme, host, and port) | That endpoint. This is the only way an explicit URL is honored, and it selects one endpoint when several are enabled. |
| a URL that matches **no** enabled endpoint (stale or malformed, including the value earlier documentation showed as an example) | Treated as stale, because the plugin has no page to edit or clear it. With exactly one enabled endpoint, that endpoint is used together with **its own** API key (the stored packer key belonged to the stale backend and is ignored too; an endpoint with no key fails closed) and a secret-free warning is written to the build log; with zero or several enabled endpoints the build fails closed with a message that names netbox-proxbox. The stored URL is never used as a transport target. |

The API key is taken from `PackerPluginSettings` when one is stored there and from
the netbox-proxbox endpoint otherwise. Secrets are never written to build logs or
error messages; the build log records only the base URL and which linkage was used.

"Exactly one endpoint" counts enabled netbox-proxbox rows. An enabled endpoint whose
request context cannot be built (for example a failed target-fingerprint check) still
counts, so it can never make a different backend look like the only one: with a single
enabled row that has no usable context the build fails closed and points at its target
fingerprint and key configuration in netbox-proxbox, and with several enabled rows it
fails closed unless the stored URL matches exactly one usable endpoint.

Requests to proxbox-api never follow HTTP redirects. A redirect response fails the
request with an error that contains only the status code, so the API key is never
forwarded to another origin or over plain HTTP.

Selecting the linkage is not authorization. The selected Proxmox endpoint must still
pass the netbox-proxbox gates (`enabled`, `allow_writes`,
`allow_packer_template_builds`) and be translated to the backend endpoint id before
dispatch and again immediately before the call to proxbox-api.

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
| Proxbox API URL | `proxbox_api_url` | Optional selector (for example `https://proxbox-api.example.invalid:8000`). Leave blank to reuse the single enabled netbox-proxbox FastAPI endpoint; see [Proxbox API linkage](#proxbox-api-linkage). |
| Proxbox API key | `proxbox_api_key_encrypted` | Optional override; when blank, the key comes from the netbox-proxbox endpoint. Set only via `set_proxbox_api_key()` / read via `get_proxbox_api_key()` — never stored or read as plaintext. See key management below. |
| Enable branching | `branching_enabled` | When `True`, staleness-check jobs run inside a netbox-branching branch. If the branching runtime is unavailable, the job fails with `BranchingUnavailableError` before writing anything; set it to `False` to explicitly allow writes to `main`. |
| Branch name prefix | `branch_name_prefix` | Prefix for auto-created branch names (default: `packer-stale`). |
| Branch conflict behavior | `branch_on_conflict` | `fail` (leave branch open) or `acknowledge` (merge anyway). |

## Proxbox API key management

The proxbox-api key is **not** stored in plain text. It is encrypted with a
Fernet cipher derived from `settings.SECRET_KEY` (SHA-256 → base64url).

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


## proxbox-api prerequisites

The `cloud_config` bake path requires:

- **Capability-bearing proxbox-api and netbox-proxbox revisions** — netbox-packer
  requires the explicit packer-template capability in both services plus the signed preflight
  contract: a non-executing build plan returns `recipe_digest`,
  `/cloud/templates/images/preflight` returns an expiring `plan_token`, and the
  execute request consumes that token. A 404 from the preflight endpoint is an
  incompatible older service and fails closed; there is no legacy one-step
  fallback. The runtime image includes `openssh-client` starting from
  `0.0.18.post1`. `proxbox-api 0.0.20` and `netbox-proxbox 0.0.25` are
  pre-capability releases; use reviewed revisions after those tags until release
  engineering records the exact validated inclusive version floors.
- **`PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION=true`** — set in the proxbox-api
  environment. Cloud image execution is disabled by default.
- **`PROXBOX_SSH_KEY_DIR`** — directory on the proxbox-api host containing the
  SSH private key that trusts the target Proxmox host.
- **`allow_writes=True`** on the target `ProxmoxEndpoint` row in netbox-proxbox.
- **`allow_packer_template_builds=True`** on that same endpoint. This separate,
  default-off capability authorizes only netbox-packer template-image creation;
  it does not replace or imply `allow_writes`.
- A `PackerTemplate.proxmox_endpoint` or enabled `PackerBuildTarget.proxmox_endpoint`
  URL that matches exactly one enabled netbox-proxbox endpoint by normalized
  host and port. Numeric build overrides do not grant endpoint authorization.
- The selected Proxmox storage pool must have the `snippets`, `import`, and
  `images` content types enabled.

For detailed host bootstrap steps (key provisioning, storage content types), see
the public proxbox-api cloud-image build documentation.

## Environment variables (proxbox-api side)

These are set on the proxbox-api service, not on the NetBox host:

| Variable | Required | Description |
| --- | --- | --- |
| `PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION` | Yes | Set to `true` to enable bake jobs |
| `PROXBOX_SSH_KEY_DIR` | Yes | Directory containing SSH private key(s) for Proxmox hosts |
| `PROXBOX_NETBOX_TIMEOUT` | No | Timeout for NetBox API calls, default `120s` |
| `PROXBOX_ENCRYPTION_KEY` | Yes | Fernet key for proxbox-api credential storage |

## Validation

After saving `PackerPluginSettings`, verify from the NetBox shell:

```python
from netbox_packer.models import PackerPluginSettings
import urllib.request

s = PackerPluginSettings.get_solo()
print("URL:", s.proxbox_api_url)
print("Key set:", bool(s.get_proxbox_api_key()))

# Quick connectivity check
req = urllib.request.Request(
    s.proxbox_api_url.rstrip("/") + "/status",
    headers={"X-Proxbox-API-Key": s.get_proxbox_api_key()},
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        print("proxbox-api reachable:", resp.status)
except Exception as exc:
    print("NOT reachable:", exc)
```
