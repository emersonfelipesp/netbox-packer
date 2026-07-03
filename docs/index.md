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

The PowerDNS co-hosted Authoritative + Recursor seed is
`powerdns-auth-recursor-ubuntu`, VMID `9019`, targeting CLUSTER01-DC01 at
`https://10.0.30.71:8006` / node `10.0.30.71`. It installs `pdns-server`,
`pdns-recursor`, and `qemu-guest-agent`, keeps authoritative on
`127.0.0.1:5300`, and restricts recursive `allow-from` ranges to internal
networks such as `10.0.0.0/8` and `172.16.0.0/12`. Never expose recursion to
`0.0.0.0/0`.

The File Server all-in-one seed is `tpl-fileserver-allinone-ubuntu-2404`, VMID
`9032`, targeting CLUSTER01-DC01 at `https://10.0.30.71:8006` / node
`10.0.30.71`. Its installer config `fileserver-allinone-cloud-config` installs
Samba AD/DC packages, Nextcloud web/PHP prerequisites, `python3-venv`, and
monitoring agents. `nms-fileserver-agent` is installed from
`NMS_FILESERVER_AGENT_PIP_SPEC`, not apt; the bake environment must provide that
pip package or source spec. The image installs
`nms-fileserver-agent-enroll.service` and
`nms-fileserver-agent-heartbeat.timer`. The baked agent config uses
`https://backend.nms.nmulti.cloud` and `https://netbox.nmulti.cloud`;
clone-time user-data supplies the per-instance enrollment token.

## Create VM Instances

Each row in the Packer Templates table has a **Create new instance** action. The
action opens a modal flow that keeps the selected `PackerTemplate` visible,
collects the proxbox-api endpoint ID, destination VMID, VM name, target node,
resource overrides, and optional cloud-init values, then submits the request to
`proxbox-api` `POST /cloud/vm/provision`.

The endpoint ID is intentionally entered by the operator: `PackerTemplate`
stores a Proxmox endpoint URL for inventory context, while proxbox-api clone
requests require the backend `ProxmoxEndpoint` primary key.

## Compatibility

| NetBox | netbox-packer | Python |
| --- | --- | --- |
| v4.5.8 | v0.0.4 | 3.12+ |
| v4.5.9 | v0.0.4 | 3.12+ |
| v4.6.0 | v0.0.4 | 3.12+ |
| v4.6.1 | v0.0.4 | 3.12+ |
| v4.6.2 | v0.0.4 | 3.12+ |
| v4.6.3 | v0.0.4 | 3.12+ |
| v4.6.4 | v0.0.4 | 3.12+ |
