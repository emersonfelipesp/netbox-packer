# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts into
NetBox through [`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api).

`netbox-packer` is part of the Proxbox plugin family but is installable as a
standalone NetBox plugin. It can be deployed alongside
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) when a
Proxmox inventory workflow needs both VM synchronization and Packer template
cataloging.

## Scope

v0.0.4 includes Packer template, build, installer-config, build-target,
staleness, HCP Packer registry sync support, and cloud-init template image
bakes through proxbox-api.

## Cloud-init Template Images

The cloud-init path stores a `#cloud-config` in `PackerInstallerConfig` and
bakes it into a Proxmox VM template through proxbox-api. See
[Cloud-init Template Images](cloud-init-template-images.md) for the operator
flow, prerequisites, and seeded examples.

The InfluxDB 2 Proxmox metrics collector seed is
`influxdb-2-ubuntu-2404-proxmox-collector`, VMID `9011`, targeting only the
development endpoint `https://10.0.30.139:8006` / node `10.0.30.139`. Do not
target the production `https://10.0.30.9:8006` / `10.0.30.9` cluster with this
seeded build process.

## Compatibility

| NetBox | netbox-packer | Python |
| --- | --- | --- |
| v4.5.8 | v0.0.4 | 3.12+ |
| v4.6.1 | v0.0.4 | 3.12+ |
