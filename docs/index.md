# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts into
NetBox through [`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api).

`netbox-packer` is part of the Proxbox plugin family but is installable as a
standalone NetBox plugin. It can be deployed alongside
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) when a
Proxmox inventory workflow needs both VM synchronization and Packer template
cataloging.

## Scope

v0.0.2.post2 includes Packer template, build, installer-config, build-target,
staleness, and HCP Packer registry sync support.

## Compatibility

| NetBox | netbox-packer | Python |
| --- | --- | --- |
| v4.5.8 | v0.0.2.post2 | 3.12+ |
| v4.6.1 | v0.0.2.post2 | 3.12+ |
