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

`netbox-packer` v0.0.5 ships Packer template, build, installer-config,
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
`snippets,import,images`. Configure `proxbox_api_url` and an encrypted API key
on the `PackerPluginSettings` singleton row from the Django/NetBox Python shell
(there is no NetBox UI page or REST endpoint for this settings model yet — see
[`docs/configuration.md`](docs/configuration.md)). Seeded examples include
Zabbix 7.4, InfluxDB OSS 2/Core 3, Kubernetes 1.31, PowerDNS, Passbolt CE, Akvorado,
a File Server all-in-one image, and base Ubuntu LTS cloud-init templates.

The Akvorado seed is `akvorado-2.4.0-ubuntu-2404`, VMID `9070`, on
CLUSTER01-DC01 at `https://10.0.30.71:8006` / node `10.0.30.71`. First boot
installs Docker Engine and the Compose plugin, then starts Kafka `4.2.0`,
Valkey `9.0`, ClickHouse `26.3`, and Akvorado `2.4.0` console, inlet, outlet,
and orchestrator through the single `akvorado.service` systemd unit. Its
credential-free default configuration is complete enough to start without a
post-boot config RPC. The template enables the optional NMS host-agent
injection against `https://backend.nms.nmulti.cloud`; existing templates keep
that injection disabled by default. Provisioned VMs retain the existing
`source_packer_template` lineage, which downstream hooks follow to the
template's read-only `provisions_service="akvorado"` marker. The console is
loopback-only on `127.0.0.1:8081`; expose it only through an SSH tunnel or a
separately provisioned authenticating reverse proxy.

The Zabbix 7.4 monitoring stack seed is
`zabbix-7.4-ubuntu-2604-pgsql-nginx`, VMID `9010`, on the development endpoint
`https://10.0.30.139:8006`. It installs Zabbix Server 7.4 + frontend + Agent 2
with a local PostgreSQL database and nginx (PHP 8.5), and initializes the
Zabbix database schema on first boot. Do not target the production
`https://10.0.30.9:8006` / `10.0.30.9` cluster with this seed.

Migration `0020` adds endpoint-agnostic, credential-free InfluxDB profiles:
`influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics` (VMID `9050`) for Proxmox
metrics/Flux and `influxdb-core-3.11.0-ubuntu-2404` (VMID `9051`) for general
SQL/InfluxQL workloads. Build requests must supply the proxbox-api
`endpoint_id` and `target_node`, with optional typed `template_vmid` and
`storage` overrides; proxbox-api derives the SSH host from that same endpoint.
Package versions are pinned and held. Initial users, databases, and
tokens are created only through typed NMS RPC and stored as `nms-secret:`
references—never in cloud-init. The legacy VMID `9011` development profile is
hardened in place and marked pending by additive migration `0020`, while the
historical `0007` migration remains immutable; the row stays development-only.

Migration `0025` adds `influxdb-core-3.11.0-debian-13` (VMID `9052`), the
**Debian 13** (Trixie) InfluxDB 3 Core profile. Unlike the Ubuntu profile above
it bakes an explicit production posture rather than package defaults: the
managed `/etc/influxdb3/influxdb3-core.conf` binds only to `127.0.0.1:8181`
with token authentication left enabled, disables telemetry upload, omits
`plugin-dir` so the Python Processing Engine stays off, and is paired with a
`influxdb3-core.service` systemd drop-in (`Restart=on-failure`). First boot
refuses to proceed on any release other than Debian 13, verifies the InfluxData
key fingerprint, installs the pinned `3.11.0` candidate, re-verifies the
installed version, holds the package, derives `node-id` from the **per-VM SMBIOS
UUID** (the clone hostname is not usable, because the clone pipeline reuses the
template's cicustom meta-data), and waits on the local `/ready` endpoint with
bounded probes and an overall deadline. Its build resolves the Trixie Debian 13
base image, and the Ubuntu/amd64-only Zabbix and NMS agent injections are disabled
for it so the composed cloud-config cannot fail on the platform it declares. It is
endpoint-agnostic and credential-free like the `0020` profiles — the first administrative token is
created and vaulted only by `service.influxdb.1.bootstrap` through typed NMS
RPC. For hosts that already exist, the audited procedures
`os.linux.debian.13.preflight_influxdb3_core` and
`os.linux.debian.13.install_influxdb3_core` apply the same posture over SSH.

The Kubernetes 1.31 seeds target CLUSTER01-DC01 at `https://10.0.30.71:8006` /
node `10.0.30.71`: a base node image `k8s-1.31-ubuntu-2404-node` (VMID `9012`)
that installs containerd + kubelet/kubeadm/kubectl 1.31 and pre-pulls
control-plane images, plus a dedicated `k8s-1.31-control-plane-ubuntu-2404`
(VMID `9013`) and `k8s-1.31-worker-node-ubuntu-2404` (VMID `9014`) pair. Run
`kubeadm init`/`kubeadm join` after cloning; these images are pre-staged nodes,
not a running cluster.

The PowerDNS co-hosted Authoritative + Recursor seed is
`powerdns-auth-recursor-ubuntu`, VMID `9019`, on Ubuntu 24.04. It installs
`pdns-server`, `pdns-backend-sqlite3`, `pdns-recursor`, and `qemu-guest-agent`.
Authoritative listens only on `127.0.0.1:5300`, while Recursor listens on the
VM primary interface on port 53 and forwards local zones to that loopback
authoritative listener. The resolver allow-list is locked to private ranges
including `10.0.0.0/8` and `172.16.0.0/12`; it must never be changed to
`0.0.0.0/0`.

The Passbolt CE seed is `passbolt-ce-ubuntu-2404`, VMID `9060`, on CLUSTER01-DC01
at `https://10.0.30.71:8006` / node `10.0.30.71` (storage `local`). It installs
the native `passbolt-ce-server` package (nginx + php-fpm + local MariaDB) for
`https://credential.nmulti.cloud` with
`PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED=true`. TLS is terminated upstream by
nginx-nms, so the guest serves plain HTTP on `:80`
(`passbolt/nginx-configuration-three-choices select none`). The QEMU guest agent
and Zabbix Agent 2 are injected at bake time; the local MariaDB password is
generated on first boot (no baked secret), and the production server OpenPGP key,
JWT keys, and database are supplied by the data migration from the existing
Passbolt instance.

The File Server all-in-one seed is `tpl-fileserver-allinone-ubuntu-2404`, VMID
`9300`, using installer config `fileserver-allinone-cloud-config` version
`1.0.1` on
CLUSTER01-DC01 at `https://10.0.30.71:8006` / node `10.0.30.71`. It installs
Samba AD/DC packages, Nextcloud web/PHP prerequisites, monitoring agents, and
`python3-venv`. `nms-fileserver-agent` is not installed through apt; the bake
creates `/opt/nms-fileserver-agent/venv` and installs
`NMS_FILESERVER_AGENT_PIP_SPEC` (default `nms-fileserver-agent==0.1.0`) from the
N-MultiCloud Gitea PyPI index. Configure
`PackerPluginSettings.fileserver_package_read_user` and the Fernet-encrypted
package-read token through `set_fileserver_package_read_token()` from the
Django/NetBox shell. Use a dedicated non-human identity whose token has only
Gitea package-Read permission; never use a personal token or
`PACKAGE_WRITE_TOKEN`. Build dispatch URL-encodes the values, fails closed when
they are missing, and writes the authenticated index to the golden image as the
root-only `/etc/nms-fileserver-agent/pip.conf`. Public `httpx` is installed from
PyPI first, and the pinned agent is then installed from the sole private index
with `--no-deps`. Operators rotate the token on the singleton settings row and
rebake VMID `9300` so future clones inherit the replacement read-only token. The
image installs
`nms-fileserver-agent-enroll.service` and
`nms-fileserver-agent-heartbeat.timer`; the baked config points at
`https://backend.nms.nmulti.cloud` and `https://netbox.nmulti.cloud`, and the
one-time enrollment token is injected only by clone-time user-data.

The base Ubuntu LTS cloud-init seeds are the customer VM catalog's starting
templates: `ubuntu-2204-cloudinit-base` (VMID `9040`), `ubuntu-2404-cloudinit-base`
(VMID `9041`), and `ubuntu-2604-cloudinit-base` (VMID `9042`), all on
CLUSTER01-DC01 at `https://10.0.30.71:8006` / node `10.0.30.71`, sharing
installer config `ubuntu-lts-base-cloud-config`. The cloud-config is
intentionally minimal — QEMU Guest Agent, Zabbix Agent 2, and `ssh_pwauth` are
all injected at build time rather than baked into the seed content. No secret
is baked into the image: per-VM username, password (Proxmox `cipassword`), and
SSH keys are supplied at clone time by Proxmox cloud-init.

## Build Dispatch and Timeouts

Build triggers create a `PackerBuild` row, set the template to `building`, and
immediately enqueue `PackerBuildJob` through the shared dispatcher using the
`build_id` keyword argument. If the queue cannot accept the job, the build is
marked `failed`, an error is appended to the build log, and the API/UI reports
that the build was not queued.

For local Packer builds, `PACKER_BUILD_TIMEOUT_SECONDS` is enforced by a
watchdog that kills the subprocess even when `packer init` or `packer build`
stalls without producing stdout.

## Create VM Instances from Templates

The Packer Templates table includes a per-row **Create new instance** action.
The button opens a NetBox modal with template confirmation, destination VMID,
target node, resource, cloud-init, and submit steps. Submission delegates to
`proxbox-api` `POST /cloud/vm/provision` using the configured
`PackerPluginSettings.proxbox_api_url` and encrypted API key.

Operators must provide the proxbox-api backend `ProxmoxEndpoint` ID in the modal
because `PackerTemplate.proxmox_endpoint` stores the Proxmox URL, not the
backend endpoint primary key. The selected template supplies the source VMID
(`proxmox_template_id`), default node, storage pool, and lineage context.

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
capture, and page-coverage workflows for NetBox v4.6.4. Docker install smoke
coverage spans NetBox v4.5.8, v4.5.9, and v4.6.0 through v4.6.4.

NetBox `4.7.x` is additionally supported on an **experimental** basis: the plugin
loads and runs with no configuration change and warns once at startup that the
line is not yet certified. See [COMPATIBILITY.md](COMPATIBILITY.md) for the tier
table, how to silence the notice, and why every Proxbox-family plugin must be
upgraded together before moving an instance to 4.7.

## License

Apache-2.0
