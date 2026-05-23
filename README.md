# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts —
Proxmox VM templates, OPNsense / pfSense appliance images, and network OS
golden images — into NetBox through the
[`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api) backend.

`netbox-packer` is part of the Proxbox plugin family but is installable as a
standalone NetBox plugin. It can be deployed alongside
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) when a
Proxmox inventory workflow needs both VM synchronization and Packer template
cataloging.

When configured with an HCP Packer organization / project ID, the plugin can
resolve image IDs from the HCP Packer registry via `proxbox-api`.

## Status

`netbox-packer` v0.0.2.post2 ships Packer template, build, installer-config,
build-target, staleness, and HCP Packer registry sync support. This post
release normalizes certification evidence, packaging metadata, and
compatibility documentation without changing runtime behavior.

## Compatibility

See [COMPATIBILITY.md](COMPATIBILITY.md) for the full version compatibility table.

## Installation

```bash
pip install netbox-packer
```

In `configuration.py`:

```python
PLUGINS = [
    "netbox_packer",
]
```

```bash
python manage.py migrate
```

## Documentation

Full documentation is published at
<https://emersonfelipesp.github.io/netbox-packer/>.

## Support

Use GitHub Issues for bugs and feature requests:
<https://github.com/emersonfelipesp/netbox-packer/issues>.

## Certification Status

Certification evidence is tracked in [CERTIFICATION.md](./CERTIFICATION.md).
The repository includes Apache-2.0 licensing, PyPI metadata, compatibility
metadata, GitHub Actions CI, release validation, docs publishing, screenshot
capture, and page-coverage workflows for NetBox v4.6.1.

## License

Apache-2.0
