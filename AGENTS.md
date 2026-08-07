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

## InfluxDB Profile Guardrail

Migration `0020` seeds OSS 2.9.1 (VMID `9050`) and Core 3.11.0 (VMID `9051`)
profiles. They are endpoint-agnostic: every build must provide a proxbox-api
`endpoint_id` and `target_node`, and the SSH host must be derived from that same
endpoint. Optional `template_vmid` and `storage` overrides select destination
identifiers. Never put a password, token, setup request, or private key in an
installer config or build override. Initial setup, database creation, and token
creation are typed NMS RPC operations backed by netbox-nms secret references.
RPC responses may expose `nms-secret:` references, never plaintext values.

The immutable historical VMID `9011` seed remains scoped to its development
endpoint (`10.0.30.139`) and must never be retargeted to production. Additive
migration `0020` replaces the database row's credential-generating content and
marks it pending without deleting any existing artifact.

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

Migration `0014_seed_fileserver_allinone_cloud_init.py` initially seeded
`tpl-fileserver-allinone-ubuntu-2404` for Ubuntu 24.04. Migration
`0017_update_fileserver_agent_package_index.py` corrects its current VMID to
`9300` and creates the current installer config,
`fileserver-allinone-cloud-config` version `1.0.1`; the verbatim cloud-config
source is `netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml`.
Migration `0022_update_fileserver_package_settings_comment.py` updates already
seeded v1.0.1 rows with the `PackerPluginSettings` /
`set_fileserver_package_read_token()` rotation instructions without modifying
historical migration 0017.

The image installs Samba AD/DC packages, Nextcloud web/PHP prerequisites,
`qemu-guest-agent`, `zabbix-agent2`, and `python3-venv`.
`nms-fileserver-agent` is installed into `/opt/nms-fileserver-agent/venv` from
`NMS_FILESERVER_AGENT_PIP_SPEC` (default `nms-fileserver-agent==0.1.0`), not
through apt. The singleton `PackerPluginSettings` row must provide
`fileserver_package_read_user` and a token set through
`set_fileserver_package_read_token()` for a dedicated non-human Gitea identity
with package-Read permission only; never use a personal token or
`PACKAGE_WRITE_TOKEN`. Dispatch fails closed when either value is missing,
redacts the token from persisted output, and bakes the sole private index into
root-only `/etc/nms-fileserver-agent/pip.conf`. Public dependencies are resolved
before the pinned agent is installed with `--no-deps`. Rotate the encrypted
settings token and rebake VMID `9300` so future clones receive the new read-only
credential. The image installs
`nms-fileserver-agent-enroll.service` and
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

## Akvorado Golden Template Guardrail

Migration `0024_seed_akvorado_cloud_init.py` seeds
`akvorado-2.4.0-ubuntu-2404` with VMID `9070` on CLUSTER01-DC01,
`https://10.0.30.71:8006` / node `10.0.30.71`. Its verbatim cloud-config is
`netbox_packer/seeds/akvorado-2.4.0-ubuntu-2404.cloud-config.yaml`.

The stack is Kafka `4.2.0`, Valkey `9.0` (never a Redis server image),
ClickHouse `26.3`, and Akvorado console/inlet/outlet/orchestrator pinned to
release `2.4.0`. The Compose lifecycle must remain wrapped by the single unit
named exactly `akvorado.service`, operating
`/opt/akvorado/docker-compose.yml`. The cloud-config must keep a working
credential-free default Akvorado configuration so first boot does not depend
on a config-deploy RPC. No container may use `latest`.

Migration `0023_packertemplate_nms_agent_and_service_marker.py` makes the NMS
host-agent injection optional and default-off. Only the Akvorado seed opts in,
points at `https://backend.nms.nmulti.cloud`, and sets the non-editable
`provisions_service="akvorado"` marker. Downstream code follows the existing
`source_packer_template` VM lineage to that marker. Agent enrollment must reuse
the existing secure-prefix bootstrap flow; never bake a token, signing key, or
new trust mechanism. Its local service allowlist contains exactly
`akvorado.service`, while the existing Zabbix injection remains responsible for
Zabbix Agent 2.
