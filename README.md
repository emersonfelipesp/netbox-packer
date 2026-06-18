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

`netbox-packer` v0.0.4 ships Packer template, build, installer-config,
build-target, staleness, and HCP Packer registry sync support, plus a
**cloud-init template image** path (see below).

## Cloud-init Template Images

A `PackerInstallerConfig` with `installer_type = "cloud_config"` holds a verbatim
`#cloud-config`. When a `PackerTemplate` using such a config is built, the plugin
delegates the real Proxmox work to `proxbox-api`
(`POST /cloud/templates/images`), which downloads the base image, writes the
cloud-config as a Proxmox `cicustom` user-data snippet, and runs `qm template` —
producing a real, bootable VM template. The flow is triggerable from the NMS UI
at `nms.nmulti.cloud/virtualization/packer` (Installer Configs + a "Create
cloud-init template image" dialog + per-row Build button).

Requirements: `proxbox-api >= 0.0.18` with
`PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION=true`, a bake SSH key trusted by the target
Proxmox host, the endpoint's `allow_writes=True`, and storage that allows
`snippets,import,images`. Configure `proxbox_api_url` + an encrypted API key on
the plugin settings page. A Zabbix 7.4 (Ubuntu 26.04, PostgreSQL + nginx)
template is seeded as a working example.

The InfluxDB 2 Proxmox metrics collector seed is
`influxdb-2-ubuntu-2404-proxmox-collector`, VMID `9011`, on the development
endpoint `https://10.0.30.139:8006` / node `10.0.30.139`. Do not target the
production `https://10.0.30.9:8006` / `10.0.30.9` cluster with this seeded bake
process. See [`docs/cloud-init-template-images.md`](docs/cloud-init-template-images.md),
`CLAUDE.md`, and the host bootstrap doc in `nmulticloud-context/deploy/docs/`.

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
capture, and page-coverage workflows for NetBox v4.6.3. Docker install smoke
coverage spans NetBox v4.5.8, v4.5.9, and v4.6.0 through v4.6.3.

## License

Apache-2.0
