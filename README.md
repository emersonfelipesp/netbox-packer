# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts —
Proxmox VM templates, OPNsense / pfSense appliance images, and network OS
golden images — into NetBox through the
[`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api) backend.

`netbox-packer` is a sibling plugin of
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox); it
reuses `netbox-proxbox` FastAPI endpoint resolution and job conventions when
that plugin is installed, and falls back to its own `proxbox_api_url` /
`proxbox_api_key` plugin settings otherwise.

When configured with an HCP Packer organization / project ID, the plugin can
resolve image IDs from the HCP Packer registry via `proxbox-api`.

## Status

`netbox-packer` v0.0.1 ships the plugin scaffold and NetBox installation
glue. Image-inventory models and HCP Packer sync land in upcoming releases.
The plugin is **read-only**: all build operations remain in Packer / HCP.

## Requirements

- NetBox 4.5.x – 4.6.x
- Python 3.12+
- [`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) `>= 0.0.16`
- A reachable [`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api)
  instance with Packer-aware endpoints enabled

## Installation

```bash
pip install netbox-packer
```

In `configuration.py`:

```python
PLUGINS = [
    "netbox_proxbox",
    "netbox_packer",
]
```

```bash
python manage.py migrate
```

## Documentation

Full documentation is published at
<https://emersonfelipesp.github.io/netbox-packer/>.

## License

Apache-2.0
