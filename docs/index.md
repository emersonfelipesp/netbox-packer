# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts into
NetBox through [`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api).

`netbox-packer` is a sibling plugin of
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) and
reuses its backend context, branch lifecycle, endpoint relationships, and
job conventions.

## Scope

v0.0.1 is a **scaffold** release: plugin registration, navigation, overview
page, packaging, docs, tests, and CI pipelines. Image-inventory models and
HCP Packer registry sync ship in subsequent releases.
