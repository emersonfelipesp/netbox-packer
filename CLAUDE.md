# CLAUDE.md — netbox-packer

## Workspace Context

This file lives at `/root/personal-context/nmulticloud-context/netbox-packer/CLAUDE.md` inside the `personal-context` workspace.
Workspace guidance: `/root/personal-context/CLAUDE.md`.
Per-repo deep-dive: `/root/personal-context/claude-reference/nmulticloud-context.md`.
Submodule layout and cross-repo links: `/root/personal-context/claude-reference/dependency-map.md`.

---

NetBox plugin for netbox-packer integration with netbox.nmulti.cloud.

## Installation

```bash
pip install -e .
python manage.py migrate
python manage.py collectstatic
```

## Development

- Pre-commit: `python -m compileall . && ruff check . && pytest tests/`
- Type checking: `pyright .`
- Full test suite: `pytest tests/ -v`

## Architecture

The plugin package is `netbox_packer/`:
- `netbox_packer/models.py` — Django ORM models (`PackerInstallerConfig`,
  `PackerTemplate`, `PackerBuild`, `PackerBuildTarget`, `PackerPluginSettings`)
- `netbox_packer/views.py` — Django UI views and viewsets
- `netbox_packer/api/` — DRF serializers and API endpoints (incl. the `build`
  action on `PackerTemplateViewSet`)
- `netbox_packer/jobs.py` — RQ background jobs (`PackerBuildJob`,
  `PackerStalenessCheckJob`) + module-level `dispatch_build()`
- `netbox_packer/proxbox_client.py` — stdlib HTTP client to proxbox-api
- `netbox_packer/migrations/` — schema + data migrations
- `netbox_packer/templates/` — Django HTML templates
- `tests/` — static (text/AST) and functional tests

## Cloud-init Template Image Bake (cloud_config path)

When a `PackerTemplate`'s `installer_config.installer_type == "cloud_config"`,
`netbox-packer` does **not** run local Packer — it **delegates the real Proxmox
template bake to `proxbox-api`**, which already holds Proxmox sessions and the
download → create → `qm template` machinery.

End-to-end flow:

```
nms UI /virtualization/packer (Create dialog -> Build)
  -> POST /api/netbox/netbox-packer/plugin/packer-templates/{id}/build/
  -> nms-backend /netbox/netbox-packer/plugin/* (generic proxy)
  -> PackerTemplateViewSet.build(): create PackerBuild -> dispatch_build(build)
  -> PackerBuildJob (RQ), cloud_config branch -> _run_proxbox_cloud_build()
       -> proxbox_client.call_proxbox_build()
       -> POST {PackerPluginSettings.proxbox_api_url}/cloud/templates/images
            header X-Proxbox-API-Key
            body { name, vmid, target_node, image_url, image_storage, vm_storage,
                   user_data_yaml = installer_config.content, execute: true, ssh_host }
  -> proxbox-api: download image -> create VM -> write cicustom user-data snippet
       on <vm_storage>:snippets -> qm template -> returns vmid
  -> PackerBuild.result_template_id=vmid, build_status=success;
     PackerTemplate.build_status=ready, built_at=now()
```

Configuration lives on the singleton `PackerPluginSettings`: `proxbox_api_url`
plus a Fernet-encrypted `proxbox_api_key_encrypted` (`set_proxbox_api_key()` /
`get_proxbox_api_key()`, keyed off `settings.SECRET_KEY` — no `netbox-nms`
dependency).

### Dispatch invariants (do not regress)

- `dispatch_build()` MUST enqueue with `PackerBuildJob.enqueue(build_id=build.pk)`
  and **never** pass `instance=build`. `PackerBuild` is not a jobs-assignable
  object type, so `instance=build` raises *"Jobs cannot be assigned to this
  object type"* and the UI Build button silently no-ops.
- `target_node` MUST collapse an unset value to `None`, never `""` — proxbox-api
  rejects an empty `target_node` with HTTP 422 (`min_length=1`).
- Both are locked by `tests/test_cloud_config_build_static.py`.

### Prerequisites (proxbox-api side)

- `proxbox-api >= 0.0.18` with `PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION=true` and
  `PROXBOX_SSH_KEY_DIR`; the runtime image bakes in `openssh-client`
  (`0.0.18.post1`). The target `ProxmoxEndpoint` needs `allow_writes=True`, and
  the chosen storage must allow `snippets,import,images` content types.
- Host bootstrap (bake SSH key, storage content types, NetBox Packer settings):
  `nmulticloud-context/deploy/docs/proxbox-api-cloud-image-bake.md`.

### Monitoring agent injection (applied at build time)

Every cloud-config build pass through `_inject_monitoring_agents()` in `jobs.py`
**before** the payload is sent to proxbox-api. The injection respects the
`PackerTemplate` model flags:

| Field | Type | Default | Effect |
|-------|------|---------|--------|
| `install_qemu_guest_agent` | bool | `True` | Adds `qemu-guest-agent` to the `packages:` list and `systemctl enable --now qemu-guest-agent` to `runcmd:`. Skipped if `qemu-guest-agent` is already in the `packages:` list. |
| `install_zabbix_agent2` | bool | `True` | Injects a Zabbix Agent 2 bootstrap script (`write_files:` + `runcmd:`). Skipped entirely if the string `"zabbix-agent2"` appears anywhere in the original cloud-config YAML. |
| `zabbix_server` | str (255) | `"zabbix.nmulti.cloud"` | `ServerActive=` directive written into the injected Zabbix agent config. |

The injection is **idempotent** — running the same template twice produces the
same cloud-config. The seeded Zabbix 7.4 template already has
`"zabbix-agent2"` in its content, so the Zabbix injection is skipped for it.
The seeded InfluxDB template already has `qemu-guest-agent` in its packages
list, so only the `systemctl enable` runcmd line is added.

The NMS `/virtualization/packer` Create dialog exposes all three fields in a
"Monitoring agents" section (both toggles default to on, `zabbix_server` input
appears when the Zabbix toggle is on). Both presets (InfluxDB, Zabbix) default
all three fields to their `PackerTemplate` defaults.

Migration `0008_packertemplate_monitoring_agents.py` adds the three fields.

### Seeded examples and migration chain

All seed migrations use `get_or_create` for idempotency. Reverse functions
are intentional no-ops (never delete operator data on rollback).

| Migration | Template name | VMID | OS | ProxmoxEndpoint | Notes |
|---|---|---|---|---|---|
| `0006` | `zabbix-7.4-ubuntu-2604-pgsql-nginx` | 9010 | Ubuntu 26.04 | `https://10.0.30.139:8006` (dev) | Zabbix 7.4 + PostgreSQL + nginx; dev host only |
| `0007` | `influxdb-2-ubuntu-2404-proxmox-collector` | 9011 | Ubuntu 24.04 | `https://10.0.30.139:8006` (dev) | InfluxDB 2.x Proxmox metrics; dev host only; do **not** target production endpoint `10.0.30.9` |
| `0008` | *(schema only — adds monitoring-agent fields)* | — | — | — | Adds `install_qemu_guest_agent`, `install_zabbix_agent2`, `zabbix_server` to `PackerTemplate` |
| `0009` | `k8s-1.31-ubuntu-2404-node` | 9012 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Kubernetes 1.31 base node (containerd + kubelet/kubeadm/kubectl, pre-pulls CP images) |
| `0010` | *(schema only — adds RegexValidator to `zabbix_server` field)* | — | — | — | `AlterField` on `PackerTemplate.zabbix_server`; no data changes |
| `0011` | `k8s-1.31-control-plane-ubuntu-2404` | 9013 | Ubuntu 24.04 | `https://10.0.30.71:8006` | K8s 1.31 control-plane (pre-pulls all CP images for fast `kubeadm init`) |
| `0011` | `k8s-1.31-worker-node-ubuntu-2404` | 9014 | Ubuntu 24.04 | `https://10.0.30.71:8006` | K8s 1.31 worker (no CP image pre-pull) |
| `0012` | `pdns-auth-ubuntu-2404` | 9017 | Ubuntu 24.04 | `https://10.0.30.71:8006` | PowerDNS Authoritative 4.9 + SQLite3 backend + REST API on 8081; DNS domain `nmulti.cloud`, nameservers `168.0.96.26`/`168.0.96.27` |
| `0012` | `pdns-recursor-ubuntu-2404` | 9018 | Ubuntu 24.04 | `https://10.0.30.71:8006` | PowerDNS Recursor 5.1 caching forwarder → `168.0.96.26`/`168.0.96.27`; allows RFC1918 clients |

#### Migration 0008 — monitoring-agent fields

Adds three fields to `PackerTemplate` used by `_inject_monitoring_agents()` at build time:

- `install_qemu_guest_agent` (BooleanField, default `True`) — injects `qemu-guest-agent` install + `systemctl enable`.
- `install_zabbix_agent2` (BooleanField, default `True`) — injects Zabbix Agent 2 bootstrap. **Injection is skipped entirely** if the installer config already contains the string `"zabbix-agent2"` (hyphen).
- `zabbix_server` (CharField, default `"zabbix.nmulti.cloud"`) — sets the `ServerActive=` directive in the injected Zabbix config.

#### Migration 0009 — Kubernetes 1.31 base node

Seeds `k8s-1.31-ubuntu-2404-node` (VMID 9012) on ProxmoxEndpoint `10.0.30.71`.
The cloud-config installs containerd, kubelet, kubeadm, kubectl 1.31, and
pre-pulls all control-plane images via `kubeadm config images pull`.
Enables `qemu-guest-agent`.

#### Migration 0012 — PowerDNS Authoritative and Recursor

Seeds two templates on ProxmoxEndpoint `10.0.30.71` (storage `local`):

**`pdns-auth-ubuntu-2404` (VMID 9017):**
- Installs `pdns-server` + `pdns-backend-sqlite3` from the official PowerDNS APT repo (suite `noble-auth-49`).
- GPG key: `https://repo.powerdns.com/FD380FBB-pub.asc`
- SQLite3 database initialized at `/var/lib/powerdns/pdns.sqlite3`.
- REST API enabled on port 8081; `api-key` placeholder must be changed before production.
- `systemd-resolved` drop-in: `DNS=168.0.96.26 168.0.96.27`, `Domains=nmulti.cloud`.
- Cloud-config does NOT contain `"zabbix-agent2"` — QEMU guest agent and
  Zabbix Agent 2 (pointing at `zabbix.nmulti.cloud`) are injected by
  `_inject_monitoring_agents()` at build time.

**`pdns-recursor-ubuntu-2404` (VMID 9018):**
- Installs `pdns-recursor` from the official PowerDNS APT repo (suite `noble-rec-51`).
- Configured as a caching forwarder: `forward-zones-recurse=.=168.0.96.26;168.0.96.27`.
- `allow-from` restricted to `127.0.0.1/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1/128`.
- Same systemd-resolved drop-in for DNS domain and nameservers.
- Same QEMU guest agent + Zabbix Agent 2 injection at build time.

Operator docs for this flow live in
`docs/cloud-init-template-images.md`. Keep that file, `README.md`, `AGENTS.md`,
and `tests/test_cloud_config_build_static.py` aligned whenever the seeded
template name, VMID, endpoint, node, cloud-init bootstrap, or production
endpoint guardrail changes.

## Automatic Staging/Production Deployment

The deploy workflow treats `develop` as staging and `main` as production.
Pushes to `develop` deploy `netbox-packer` to
`https://staging.netbox.nmulti.cloud`; pushes to `main` deploy to
`https://netbox.nmulti.cloud`.

**Deploy job in `.gitea/workflows/deploy-production.yml`:**
- Triggers on `push: [develop, main]` branch updates
- Also supports manual dispatch via `workflow_dispatch` with optional `ref` and optional `environment` choice
- Runs on `prod-deploy` runner with access to the NetBox deploy host
- For staging, executes `/opt/nmulticloud/deploy/bin/deploy-netbox-plugin-staging netbox-packer "$REF"`
- For production, executes `/opt/nmulticloud/deploy/bin/deploy-netbox-plugin netbox-packer "$REF"` when local, or falls back to `ssh nmc-prod-207 -- deploy-plugin netbox-packer "$REF"`
- Keep the full plugin slug `netbox-packer`; the production deploy helper validates repository-style NetBox plugin names and rejects the short historical slug `packer`.

**Deploy parameters:**
- REF: can be a version tag (v0.1.0), branch name (main/develop), or 7+ character commit SHA
- Default: uses current commit SHA if not specified in manual dispatch

**Security hardening:**
- REF is passed via environment variable, not direct GitHub Actions context interpolation
- Bash case statement validates ref format before SSH (whitelist: version tags, branch names, commit SHAs)
- StrictHostKeyChecking=accept-new prevents MITM attacks
- Quoted variable interpolation prevents shell injection

**Deployment on target server (`nmc-prod-207`):**
1. Git fetch/checkout of the specified ref in the plugin submodule
2. pip install -e to refresh editable install and pick up new dependencies
3. manage.py migrate to apply any pending migrations
4. manage.py collectstatic to collect new/updated static files
5. Reload/restart the target NetBox web and worker services
6. Health check the selected endpoint to verify the service is responding

**Monitoring and verification:**
- Watch the `deploy-production.yml` workflow run in Gitea Actions
- Check the `deploy` job logs for SSH output and health check results
- Verify staging: `curl -fsS https://staging.netbox.nmulti.cloud/api/`
- Verify production: `curl -fsS https://netbox.nmulti.cloud/api/`
- Check service logs: `ssh nmc-prod-207 -- logs netbox`

**Manual deployment trigger:**
```bash
# Deploy a specific tag or branch via workflow dispatch
nms git actions run netbox-packer .gitea/workflows/deploy-production.yml \
  -r main -f environment=production -f ref=v0.1.0

# Or SSH directly to production
ssh nmc-prod-207 -- deploy-plugin netbox-packer v0.1.0
```

For comprehensive deploy infrastructure documentation, see `/root/personal-context/nmulticloud-context/CLAUDE.md` section "Automatic Plugin Deployment to Production".
