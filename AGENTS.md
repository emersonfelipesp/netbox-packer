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
