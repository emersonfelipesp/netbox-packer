# AGENTS.md - netbox-packer

This file mirrors the sibling `CLAUDE.md` guidance for agents that read
`AGENTS.md`. Treat `CLAUDE.md` as the source material.

## Source

@CLAUDE.md

## Zabbix Monitoring Stack Image Guardrail

The seeded Zabbix 7.4 monitoring server template is
`zabbix-7.4-ubuntu-2604-pgsql-nginx` with VMID `9010`. The seed and bake
process target only the development Proxmox endpoint
`https://10.0.30.139:8006` / node `10.0.30.139`.

Do not point this seeded template process at the production
`https://10.0.30.9:8006` / `10.0.30.9` cluster. Keep the project docs,
`CLAUDE.md`, this file, and `tests/test_cloud_config_build_static.py` aligned
when changing the cloud-init template image flow.

## InfluxDB Collector Image Guardrail

The seeded InfluxDB 2 collector template is
`influxdb-2-ubuntu-2404-proxmox-collector` with VMID `9011`. The seed and bake
process target only the development Proxmox endpoint
`https://10.0.30.139:8006` / node `10.0.30.139`.

Do not point this seeded template process at the production
`https://10.0.30.9:8006` / `10.0.30.9` cluster. Keep the project docs,
`CLAUDE.md`, this file, and `tests/test_cloud_config_build_static.py` aligned
when changing the cloud-init template image flow.

## Build Dispatch Guardrail

Every UI, API, or maintenance trigger that creates a `PackerBuild` must call the
shared `dispatch_build(build)` helper immediately after setting the template to
`build_status="building"`. `dispatch_build()` must enqueue with
`PackerBuildJob.enqueue(build_id=build.pk)` only; never pass `instance=build`.
If enqueue fails, the build is marked `failed`, an error is written to the build
log, and the template returns to `failed` unless another build is active.

Local `packer init` / `packer build` subprocesses must enforce
`PACKER_BUILD_TIMEOUT_SECONDS` even during silent stalls with no stdout.

## PowerDNS Co-hosted Resolver Seed

Migration `0013_seed_powerdns_auth_recursor_cloud_init.py` seeds
`powerdns-auth-recursor-ubuntu` with VMID `9019` for Ubuntu 24.04. It installs
`pdns-server`, `pdns-backend-sqlite3`, `pdns-recursor`, and `qemu-guest-agent`
in one cloud-init template image.

Authoritative listens only on `127.0.0.1:5300`; Recursor listens on the VM
primary interface on port 53 and forwards local zones to the loopback
authoritative service. Its `allow-from` guardrail is restricted to private
ranges including `10.0.0.0/8` and `172.16.0.0/12`; never change it to
`0.0.0.0/0`.

The default bake target is CLUSTER01-DC01, `https://10.0.30.71:8006` / node
`10.0.30.71`, with operator overrides still available at build dispatch.

## Passbolt CE Seed

Migration `0015_seed_passbolt_cloud_init.py` seeds `passbolt-ce-ubuntu-2404` with
VMID `9060` for Ubuntu 24.04 on CLUSTER01-DC01, `https://10.0.30.71:8006` / node
`10.0.30.71`. The installer config is `passbolt-ce-ubuntu-2404` and the verbatim
cloud-config is `netbox_packer/seeds/passbolt-ce-ubuntu-2404.cloud-config.yaml`.
It installs the native `passbolt-ce-server` package (nginx + php-fpm + local
MariaDB) for `credential.nmulti.cloud` with
`PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED=true`; TLS terminates upstream so the
guest serves plain HTTP on `:80`. The QEMU guest agent and Zabbix Agent 2 are
injected at bake time. No secret is baked: the local DB password is generated on
first boot, and the server key, JWT keys, and database come from the data
migration off the existing instance.

## File Server All-in-One Seed

Migration `0014_seed_fileserver_allinone_cloud_init.py` seeds
`tpl-fileserver-allinone-ubuntu-2404` with VMID `9032` for Ubuntu 24.04. The
installer config is `fileserver-allinone-cloud-config`, and the verbatim
cloud-config source is `netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml`.

The image installs Samba AD/DC packages, Nextcloud web/PHP prerequisites,
`qemu-guest-agent`, `zabbix-agent2`, and `python3-venv`.
`nms-fileserver-agent` is installed into `/opt/nms-fileserver-agent/venv` from
`NMS_FILESERVER_AGENT_PIP_SPEC` (default `nms-fileserver-agent==0.1.0`), not
through apt. The bake source must provide that pip package or direct source
spec. The image installs `nms-fileserver-agent-enroll.service` and
`nms-fileserver-agent-heartbeat.timer`; it points the agent at
`https://backend.nms.nmulti.cloud` and `https://netbox.nmulti.cloud`; do not
bake a tenant enrollment token into the image. The default bake target is
CLUSTER01-DC01, `https://10.0.30.71:8006` / node `10.0.30.71`.

## Base Ubuntu LTS Cloud-init Seed

Migration `0016_seed_ubuntu_lts_base_cloud_init.py` seeds the customer VM
catalog's starting templates: `ubuntu-2204-cloudinit-base` (VMID `9040`),
`ubuntu-2404-cloudinit-base` (VMID `9041`), and `ubuntu-2604-cloudinit-base`
(VMID `9042`), all on CLUSTER01-DC01, `https://10.0.30.71:8006` / node
`10.0.30.71`, sharing installer config `ubuntu-lts-base-cloud-config`. This is
a **production** endpoint, unlike the InfluxDB/Zabbix dev-only seeds above —
do not confuse it with the development host `10.0.30.139`.

The cloud-config content is intentionally minimal: `qemu-guest-agent`,
`zabbix-agent2`, and `ssh_pwauth: true` are added by the build-time
monitoring-agent injection in `jobs.py`, not baked into the seed. No secret is
baked into the image — per-VM username, password (Proxmox `cipassword`), and
SSH keys are supplied at clone time by Proxmox cloud-init. This is also the
only seed migration whose reverse function is fully reversible (it deletes the
three seeded rows on rollback); every other seed migration's reverse function
is a no-op to avoid destroying operator data.
