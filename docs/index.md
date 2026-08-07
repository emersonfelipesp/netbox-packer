# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts into
NetBox through [`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api).

`netbox-packer` is part of the Proxbox plugin family but is installable as a
standalone NetBox plugin. It can be deployed alongside
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) when a
Proxmox inventory workflow needs both VM synchronization and Packer template
cataloging.

## Scope

v0.0.5 includes Packer template, build, installer-config, build-target,
staleness, HCP Packer registry sync support, and cloud-init template image
bakes through proxbox-api.

## Cloud-init Template Images

The cloud-init path stores a `#cloud-config` in `PackerInstallerConfig` and
bakes it into a Proxmox VM template through proxbox-api. See
[Cloud-init Template Images](cloud-init-template-images.md) for the operator
flow, prerequisites, and seeded examples.

The Zabbix 7.4 monitoring stack seed is `zabbix-7.4-ubuntu-2604-pgsql-nginx`,
VMID `9010`, targeting the development endpoint `https://10.0.30.139:8006`. It
installs Zabbix Server 7.4, the PHP frontend, and Agent 2 on Ubuntu 26.04 with
a local PostgreSQL database and nginx (PHP 8.5), initializing the Zabbix
database schema on first boot. Do not target the production
`https://10.0.30.9:8006` / `10.0.30.9` cluster with this seed.

The Akvorado seed is `akvorado-2.4.0-ubuntu-2404`, VMID `9070`, on
CLUSTER01-DC01 (`https://10.0.30.71:8006` / `10.0.30.71`). Its first-boot
cloud-config installs Docker and starts the pinned Kafka `4.2.0`, Valkey `9.0`,
ClickHouse `26.3`, and Akvorado `2.4.0` stack under `akvorado.service`. It ships
a working credential-free default configuration and opts into NMS host-agent
self-registration; the agent stays opt-in for every other template. The console
binds only to `127.0.0.1:8081` and requires an SSH tunnel or a separately
provisioned authenticating reverse proxy.

InfluxDB is available as two endpoint-agnostic profiles: OSS `2.9.1` for
Proxmox metrics/Flux (VMID `9050`) and Core `3.11.0` for general-purpose
SQL/InfluxQL workloads (VMID `9051`). Build dispatch supplies proxbox-api
`endpoint_id` and `target_node` explicitly. Cloud-init contains no credentials or
product setup call; typed NMS RPC owns onboarding and netbox-nms owns encrypted
secret material exposed only as `nms-secret:` references. The legacy VMID
`9011` profile remains development-only and is
hardened/marked pending by migration `0020`.

The Kubernetes 1.31 seeds target CLUSTER01-DC01 at `https://10.0.30.71:8006` /
node `10.0.30.71`: a base node image `k8s-1.31-ubuntu-2404-node` (VMID `9012`)
installing containerd and kubelet/kubeadm/kubectl 1.31 with control-plane
images pre-pulled, plus a dedicated `k8s-1.31-control-plane-ubuntu-2404` (VMID
`9013`) and `k8s-1.31-worker-node-ubuntu-2404` (VMID `9014`) pair. `kubeadm
init`/`kubeadm join` still runs after cloning.

The PowerDNS co-hosted Authoritative + Recursor seed is
`powerdns-auth-recursor-ubuntu`, VMID `9019`, targeting CLUSTER01-DC01 at
`https://10.0.30.71:8006` / node `10.0.30.71`. It installs `pdns-server`,
`pdns-recursor`, and `qemu-guest-agent`, keeps authoritative on
`127.0.0.1:5300`, and restricts recursive `allow-from` ranges to internal
networks such as `10.0.0.0/8` and `172.16.0.0/12`. Never expose recursion to
`0.0.0.0/0`.

The Passbolt CE seed is `passbolt-ce-ubuntu-2404`, VMID `9060`, targeting
CLUSTER01-DC01 at `https://10.0.30.71:8006` / node `10.0.30.71`. Its installer
config `passbolt-ce-ubuntu-2404` installs the native `passbolt-ce-server` package
(nginx + php-fpm + local MariaDB) for `credential.nmulti.cloud` with
`PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED=true`. TLS terminates upstream at
nginx-nms so the guest serves plain HTTP on `:80`. QEMU guest agent and Zabbix
Agent 2 are injected at bake time; the DB password is generated on first boot and
the server key/JWT/database come from the data migration.

The File Server all-in-one seed is `tpl-fileserver-allinone-ubuntu-2404`, VMID
`9300`, targeting CLUSTER01-DC01 at `https://10.0.30.71:8006` / node
`10.0.30.71`. Its installer config `fileserver-allinone-cloud-config` v1.0.1 installs
Samba AD/DC packages, Nextcloud web/PHP prerequisites, `python3-venv`, and
monitoring agents. `nms-fileserver-agent` is installed from
`NMS_FILESERVER_AGENT_PIP_SPEC`, not apt. The singleton `PackerPluginSettings`
row holds the plaintext `fileserver_package_read_user` and the
Fernet-encrypted token, set through `set_fileserver_package_read_token()`, for a
dedicated non-human Gitea identity with package-Read permission only. Dispatch
fails closed without either value and bakes the sole authenticated index into
root-only `/etc/nms-fileserver-agent/pip.conf`; operators rotate the settings
token and rebake VMID `9300`. The image installs
`nms-fileserver-agent-enroll.service` and
`nms-fileserver-agent-heartbeat.timer`. The baked agent config uses
`https://backend.nms.nmulti.cloud` and `https://netbox.nmulti.cloud`;
clone-time user-data supplies the per-instance enrollment token.

The base Ubuntu LTS cloud-init seeds — `ubuntu-2204-cloudinit-base` (VMID
`9040`), `ubuntu-2404-cloudinit-base` (VMID `9041`), and
`ubuntu-2604-cloudinit-base` (VMID `9042`), all on CLUSTER01-DC01 at
`https://10.0.30.71:8006` / node `10.0.30.71` — are the starting point for the
customer VM catalog. They share installer config
`ubuntu-lts-base-cloud-config`, a minimal `#cloud-config`; QEMU Guest Agent,
Zabbix Agent 2, and `ssh_pwauth` are injected at build time rather than baked
in. No secret is baked into the image — username, password, and SSH keys come
from Proxmox cloud-init at clone time.

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
| v4.5.8 | v0.0.5 | 3.12+ |
| v4.5.9 | v0.0.5 | 3.12+ |
| v4.6.0 | v0.0.5 | 3.12+ |
| v4.6.1 | v0.0.5 | 3.12+ |
| v4.6.2 | v0.0.5 | 3.12+ |
| v4.6.3 | v0.0.5 | 3.12+ |
| v4.6.4 | v0.0.5 | 3.12+ |
