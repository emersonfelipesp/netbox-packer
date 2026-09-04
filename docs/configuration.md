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
| Proxbox API URL | `proxbox_api_url` | Base URL of the proxbox-api backend (e.g. `http://10.0.30.207:8000`). Required for `cloud_config` installer-type builds. |
| Proxbox API key | `proxbox_api_key_encrypted` | Set only via `set_proxbox_api_key()` / read via `get_proxbox_api_key()` — never stored or read as plaintext. See key management below. |
| File Server package-read user | `fileserver_package_read_user` | Plaintext username for the dedicated non-human Gitea package reader. |
| File Server package-read token | `fileserver_package_read_token_encrypted` | Set only via `set_fileserver_package_read_token()` / read via `get_fileserver_package_read_token()` — never stored or read as plaintext. |
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

- **Capability-bearing proxbox-api and netbox-proxbox revisions** — netbox-packer
  requires the explicit packer-template capability in both services plus the signed preflight
  contract: a non-executing build plan returns `recipe_digest`,
  `/cloud/templates/images/preflight` returns an expiring `plan_token`, and the
  execute request consumes that token. A 404 from the preflight endpoint is an
  incompatible older service and fails closed; there is no legacy one-step
  fallback. The runtime image includes `openssh-client` starting from
  `0.0.18.post1`. `proxbox-api 0.0.20` and `netbox-proxbox 0.0.25.post1` are
  the reviewed capability releases; use reviewed revisions after those tags until release
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
the `nmulticloud-context` deploy documentation at
`deploy/docs/proxbox-api-cloud-image-bake.md`.

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
