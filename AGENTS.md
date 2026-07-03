# AGENTS.md - netbox-packer

This file mirrors the sibling `CLAUDE.md` guidance for agents that read
`AGENTS.md`. Treat `CLAUDE.md` as the source material.

## Source

@CLAUDE.md

## InfluxDB Collector Image Guardrail

The seeded InfluxDB 2 collector template is
`influxdb-2-ubuntu-2404-proxmox-collector` with VMID `9011`. The seed and bake
process target only the development Proxmox endpoint
`https://10.0.30.139:8006` / node `10.0.30.139`.

Do not point this seeded template process at the production
`https://10.0.30.9:8006` / `10.0.30.9` cluster. Keep the project docs,
`CLAUDE.md`, this file, and `tests/test_cloud_config_build_static.py` aligned
when changing the cloud-init template image flow.

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
