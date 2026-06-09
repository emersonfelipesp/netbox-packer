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

### Seeded examples: Zabbix 7.4 and InfluxDB 2.x

Migration `0006_seed_zabbix_cloud_init.py` seeds a `cloud_config`
`PackerInstallerConfig` + `PackerTemplate`
(`zabbix-7.4-ubuntu-2604-pgsql-nginx`, Ubuntu 26.04, storage `local`, VMID
9010). The `#cloud-config` installs Zabbix 7.4 server + frontend + agent2,
PostgreSQL + nginx (PHP 8.5).

Migration `0007_seed_influxdb_cloud_init.py` seeds the InfluxDB 2.x Proxmox
metrics collector template (`influxdb-2-ubuntu-2404-proxmox-collector`, Ubuntu
24.04, storage `local`, VMID 9011). It installs InfluxDB from the official
InfluxData APT repository, enables `influxdb` and `qemu-guest-agent`, initializes
org `nmulticloud` and bucket `proxmox` through the local InfluxDB setup API, and
writes generated credentials to `/etc/nmulticloud/influxdb-collector.env` on the
cloned VM. The seed targets only the development ProxmoxEndpoint
`https://10.0.30.139:8006`; do not point this seeded build at the production
`10.0.30.9` cluster.

## Automatic Production Deployment

**Starting with the deploy-production workflow**, new commits to `main` automatically deploy to `netbox.nmulti.cloud`.

**Deploy job in `.gitea/workflows/deploy-production.yml`:**
- Triggers on `push: [main]` branch updates
- Also supports manual dispatch via `workflow_dispatch` with optional `ref` input
- Runs on `prod-deploy` runner with SSH access to production host
- Executes `/opt/nmulticloud/deploy/bin/deploy-netbox-plugin netbox-packer "$REF"`
  when the production deploy helper is local, otherwise falls back to
  `ssh nmc-prod-207 -- deploy-plugin netbox-packer "$REF"`.
- Keep the full plugin slug `netbox-packer`; the production deploy helper
  validates repository-style NetBox plugin names and rejects the short
  historical slug `packer`.

**Deploy parameters:**
- REF: can be a version tag (v0.1.0), branch name (main/develop), or 7+ character commit SHA
- Default: uses current commit SHA if not specified in manual dispatch

**Security hardening:**
- REF is passed via environment variable, not direct GitHub Actions context interpolation
- Bash case statement validates ref format before SSH (whitelist: version tags, branch names, commit SHAs)
- StrictHostKeyChecking=accept-new prevents MITM attacks
- Quoted variable interpolation prevents shell injection

**Deployment on production server (`nmc-prod-207`):**
1. Git fetch/checkout of the specified ref in the plugin submodule
2. pip install -e to refresh editable install and pick up new dependencies
3. manage.py migrate to apply any pending migrations
4. manage.py collectstatic to collect new/updated static files
5. systemctl reload netbox-production (graceful gunicorn reload)
6. systemctl restart netbox-rq (RQ worker restart for code changes)
7. Health check: curl -sf http://127.0.0.1:18001/api/ to verify service is responding

**Monitoring and verification:**
- Watch the `deploy-production.yml` workflow run in Gitea Actions
- Check the `deploy` job logs for SSH output and health check results
- Verify production is healthy: `ssh nmc-prod-207 -- health netbox`
- Check service logs: `ssh nmc-prod-207 -- logs netbox`

**Manual deployment trigger:**
```bash
# Deploy a specific tag or branch via workflow dispatch
nms git actions run netbox-packer .gitea/workflows/deploy-production.yml \
  -r main -f ref=v0.1.0

# Or SSH directly to production
ssh nmc-prod-207 -- deploy-plugin netbox-packer v0.1.0
```

For comprehensive deploy infrastructure documentation, see `/root/personal-context/nmulticloud-context/CLAUDE.md` section "Automatic Plugin Deployment to Production".
