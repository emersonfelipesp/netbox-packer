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

After running migrations, the singleton settings row is available in NetBox under
**Plugins → Packer Plugin Settings**. All fields can be edited through the UI or
the REST API.

| Setting | UI label | Description |
| --- | --- | --- |
| `proxbox_api_url` | Proxbox API URL | Base URL of the proxbox-api backend (e.g. `http://10.0.30.207:8000`). Required for `cloud_config` installer-type builds. |
| `proxbox_api_key` | Proxbox API key | Plaintext key entry (stored Fernet-encrypted). See key management below. |
| `branching_enabled` | Enable branching | When `True`, staleness-check jobs run inside a netbox-branching branch. |
| `branch_name_prefix` | Branch name prefix | Prefix for auto-created branch names (default: `packer-stale`). |
| `branch_on_conflict` | Branch conflict behavior | `fail` (leave branch open) or `acknowledge` (merge anyway). |

## Proxbox API key management

The `proxbox_api_key` is **not** stored in plain text. It is encrypted with a
Fernet cipher derived from `settings.SECRET_KEY` (SHA-256 → base64url). There
is **no dependency on `netbox-nms`** for this encryption.

### Storing the key

**Via the NetBox UI.** Edit the Packer Plugin Settings object and enter the key
in the **Proxbox API key** field. The plugin encrypts it on `save()`.

**Via the Python shell.** This is useful for automation or initial bootstrap:

```python
from netbox_packer.models import PackerPluginSettings

settings_row = PackerPluginSettings.get_solo()
settings_row.set_proxbox_api_key("your-proxbox-api-key-here")
settings_row.save()
```

**Via the REST API.** `POST /api/plugins/packer/plugin-settings/` (or `PATCH` the
existing row) with the `proxbox_api_key` field in the request body. The
serializer handles encryption transparently.

### Verifying the key

```python
from netbox_packer.models import PackerPluginSettings

settings_row = PackerPluginSettings.get_solo()
key = settings_row.get_proxbox_api_key()
print("Key configured:", bool(key))
```

## proxbox-api prerequisites

The `cloud_config` bake path requires:

- **`proxbox-api >= 0.0.18`** — the `user_data_yaml` parameter and `cicustom`
  snippet writing were introduced in this version. The runtime image includes
  `openssh-client` starting from `0.0.18.post1`.
- **`PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION=true`** — set in the proxbox-api
  environment. Cloud image execution is disabled by default.
- **`PROXBOX_SSH_KEY_DIR`** — directory on the proxbox-api host containing the
  SSH private key that trusts the target Proxmox host.
- **`allow_writes=True`** on the target `ProxmoxEndpoint` row in netbox-proxbox.
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
