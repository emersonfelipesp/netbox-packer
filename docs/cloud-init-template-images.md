# Cloud-init Template Images

`netbox-packer` can store a cloud-init user-data document in
`PackerInstallerConfig` and bake it into a Proxmox VM template through
`proxbox-api`. The plugin does not run local Packer for this path. It creates a
`PackerBuild`, queues `PackerBuildJob`, and delegates the real Proxmox work to
`POST /cloud/templates/images` on `proxbox-api`.

## Flow

1. Create or select a `PackerInstallerConfig` where
   `installer_type = "cloud_config"`.
2. Create or select a `PackerTemplate` that references the installer config.
3. Trigger the template build from NetBox, the API, or the NMS page at
   `/virtualization/packer`.
4. `PackerBuildJob` resolves the build node, base cloud image URL, storage, and
   SSH host.
5. `proxbox-api` downloads the base image, creates the VM, writes the cloud-init
   snippet as Proxmox `cicustom` user-data, converts the VM to a template with
   `qm template`, and returns the resulting VMID.

The plugin settings must include `proxbox_api_url` and an encrypted
`proxbox_api_key`. The target `ProxmoxEndpoint` in netbox-proxbox must allow
writes, and the selected Proxmox storage must support `snippets`, `import`, and
`images`.

## InfluxDB 2 Collector Template

Migration `0007_seed_influxdb_cloud_init.py` seeds the InfluxDB collector image
used for Proxmox cluster metrics collectors.

| Field | Value |
| --- | --- |
| Template name | `influxdb-2-ubuntu-2404-proxmox-collector` |
| Installer config | `influxdb-2-ubuntu-2404-proxmox-collector` |
| OS | Ubuntu `24.04` |
| Template VMID | `9011` |
| Proxmox endpoint | `https://10.0.30.139:8006` |
| Proxmox node / SSH host | `10.0.30.139` |
| Storage | `local` |
| Default InfluxDB org | `nmulticloud` |
| Default InfluxDB bucket | `proxmox` |
| Default retention | `2592000` seconds |

Guardrail: `https://10.0.30.9:8006` / `10.0.30.9` is the production
`netbox.nmulti.cloud` Proxmox cluster. Do not seed, bake, or retarget this
collector template there. The seeded build target is the development endpoint
`https://10.0.30.139:8006` only.

The cloud-init payload installs `influxdb2` from the official InfluxData APT
repository after verifying key fingerprint
`24C975CBA61A024EE1B631787C3D57159FC2F927`. It enables `influxdb` and
`qemu-guest-agent`, initializes the local InfluxDB setup API when setup is
still allowed, and writes generated connection material to
`/etc/nmulticloud/influxdb-collector.env` with mode `0600`.

Per-clone overrides can be provided before first boot with these environment
variables:

| Variable | Purpose |
| --- | --- |
| `INFLUXDB_USERNAME` | Initial admin user, default `admin` |
| `INFLUXDB_PASSWORD` | Initial admin password, default random |
| `INFLUXDB_ORG` | Initial org, default `nmulticloud` |
| `INFLUXDB_BUCKET` | Initial bucket, default `proxmox` |
| `INFLUXDB_RETENTION_SECONDS` | Bucket retention, default `2592000` |
| `INFLUXDB_ADMIN_TOKEN` | Admin token, default random |

## PowerDNS Authoritative + Recursor Template

Migration `0013_seed_powerdns_auth_recursor_cloud_init.py` seeds a co-hosted
PowerDNS image for internal DNS service VMs.

| Field | Value |
| --- | --- |
| Template name | `powerdns-auth-recursor-ubuntu` |
| Installer config | `powerdns-auth-recursor-ubuntu` |
| OS | Ubuntu `24.04` |
| Template VMID | `9019` |
| Proxmox endpoint | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` |
| Storage | `local` |
| Authoritative listener | `127.0.0.1:5300` |
| Recursor listener | VM primary IPv4 address on port `53` |

The default bake target is the CLUSTER01-DC01 PVE cluster host
`10.0.30.71`. Operators may override the node or VMID at build dispatch when a
different target is needed.

The cloud-init payload installs `pdns-server`, `pdns-backend-sqlite3`,
`pdns-recursor`, `qemu-guest-agent`, `sqlite3`, and `iproute2`. The
authoritative service uses the bundled SQLite3 backend and initializes the
`gsqlite3` schema at `/var/lib/powerdns/pdns.sqlite3`.

Authoritative is loopback-only:

- DNS: `local-address=127.0.0.1`, `local-port=5300`
- API: `webserver=yes`, `webserver-address=127.0.0.1`, `webserver-port=8081`
- API key placeholder: `PDNS_AUTH_API_KEY`

Recursor is the resolver surface:

- DNS: VM primary IPv4 on port 53
- API: `webserver=yes`, `webserver-address=127.0.0.1`, `webserver-port=8082`
- Local-zone forwarding: `PDNS_LOCAL_FORWARD_ZONES`, default
  `nmulti.cloud=127.0.0.1:5300`
- Optional recursive forwarding: `PDNS_LOCAL_FORWARD_ZONES_RECURSE` appends a
  `forward-zones-recurse` entry when provided
- API key placeholder: `PDNS_RECURSOR_API_KEY`

The recursor `allow-from` list is restricted to `127.0.0.1/8`, `10.0.0.0/8`,
`172.16.0.0/12`, `192.168.0.0/16`, and `::1/128`. Do not set it to
`0.0.0.0/0`; this template must not become an open resolver.

## Build Verification

After the build completes, the template row should have:

- `build_status = "ready"`
- `proxmox_template_id = 9011`
- `proxmox_endpoint = "https://10.0.30.139:8006"`
- `proxmox_node = "10.0.30.139"`

On the Proxmox development endpoint, VMID `9011` should be marked as a template
and include a cloud-init `cicustom` user-data snippet. The production endpoint
`10.0.30.9` must remain untouched by this process.

For the PowerDNS co-hosted template, VMID `9019` should be marked as a template
on `10.0.30.71`. On first boot from a clone, `pdns` should listen on
`127.0.0.1:5300`, `pdns-recursor` should listen on the primary IPv4 address on
port 53, both PowerDNS API webservers should bind to localhost, and no
configuration should expose recursion to `0.0.0.0/0`.

## Regression Coverage

`tests/test_cloud_config_build_static.py` locks the cloud-init build contract:

- the cloud-config branch delegates to `proxbox-api /cloud/templates/images`;
- unset target nodes are sent as `None`, not an empty string;
- the InfluxDB seed keeps template name, VMID, development endpoint, node, and
  storage stable;
- the InfluxDB cloud-config keeps the InfluxData key verification, package
  install, setup API call, org/bucket/retention defaults, guest agent, and
  credential file;
- project docs and LLM files mention the template identity and production
  endpoint guardrail.
- the PowerDNS co-hosted seed keeps `pdns-server`, `pdns-recursor`,
  `qemu-guest-agent`, `127.0.0.1:5300`, private `allow-from` ranges, and
  reversible seeded-row cleanup stable.
