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
