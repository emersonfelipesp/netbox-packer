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
- `netbox_packer/static/netbox_packer/` — plugin static assets (e.g.
  `os_version_filter.js`)
- `tests/` — static (text/AST) and functional tests

## Template form UX (`PackerTemplateForm`)

The template add/edit form is tuned for creating cloud-init templates:

- **`os_version` is a grouped dropdown that narrows to the selected OS family**,
  not free text. Options live in the single `OS_VERSIONS_BY_FAMILY` mapping in
  `choices.py` (helpers `os_version_grouped_choices()` /
  `os_version_known_values()`) and are rendered as optgroups by OS family. The
  static `os_version_filter.js` (loaded via the form's `Media`) narrows the
  visible list to the family selected in `os_family`, using the
  `data-os-version-map` JSON on the widget; the grouped `<select>` still works
  without JavaScript.
  - **The `os_version` widget carries the `no-ts` class** so NetBox does **not**
    wrap it in Tom Select. Tom Select owns the rendered dropdown from its own
    option registry, so the native `.add()/.remove()` narrowing in the JS would
    silently no-op against it — the field would stay cross-selectable with any
    family. `no-ts` keeps the plain `<select>` (and the JS) authoritative.
  - **Family change resets the version.** On initial load the JS preserves the
    server-rendered value (edit case); when the user *changes* `os_family` it
    clears `os_version` so a stale value from the previous family can never
    linger (e.g. `Ubuntu` can never keep `Debian 13` selected).
  - **`PackerTemplateForm.clean()`** is the server-side guard: it rejects an
    `os_version` that is not in `OS_VERSIONS_BY_FAMILY[os_family]` (covering
    JavaScript-disabled submits), while still allowing an instance's
    originally-stored (off-list) value on edit. Scope is this **UI form only**.
  - **`clean()` reads `super().clean() or self.cleaned_data`, not just
    `super().clean()`.** Under NetBox 4.6.4, `CheckLastUpdatedMixin.clean()`
    (in the `NetBoxModelForm` MRO between this form and `forms.ModelForm`)
    returns `None` unconditionally for any new/unsaved-instance form, which
    crashed every "create new PackerTemplate" submission with
    `AttributeError: 'NoneType' object has no attribute 'get'`. Django's
    `BaseForm._clean_form()` only overwrites `self.cleaned_data` when an
    overridden `clean()` returns non-`None`, so falling back to
    `self.cleaned_data` is safe and keeps the `_clean_fields()`-populated
    data. If NetBox fixes the core mixin, the `or self.cleaned_data` fallback
    becomes a no-op — leave it in place rather than reverting it.
  - The model field and REST API stay a plain `CharField` — **no migration** and
    automation can still POST any version string (the free-form contract is
    intentional; do not add serializer validation).
  - The form's `__init__` re-adds an instance's stored `os_version` if it is not
    in the offered list (labelled `… (current)`), so editing an older template
    never fails validation. The JS mirrors this (keeps an off-list value).
  - Add a new offered version by appending a `("<ver>", "<label>")` tuple to the
    relevant family list in `OS_VERSIONS_BY_FAMILY`; no migration needed.
- **Machine-managed fields are hidden** from the form (`built_at`,
  `packer_template_ref`, `installer_config_checksum_at_build`,
  `base_image_url_at_build`, `base_image_sha256_at_build`) — they are written
  by `PackerBuildJob` and remain available read-only via the REST API.
- **Help text** guides `os_version`, `proxmox_template_id`, `storage_pool`,
  `cloud_init_ready`, and `installer_config`.

Verify form changes against a live NetBox runtime (per the workspace NetBox form
guardrail), not only the static AST tests in
`tests/test_plugin_structure_static.py`.

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
       -> PLAN: POST /cloud/templates/images with execute=false
            <- server-authored recipe_digest
       -> PREFLIGHT: POST /cloud/templates/images/preflight (contract 1.0)
            body { endpoint_id, target_node, vmid, provider, storage fields,
                   recipe_digest, snippets_required }
            <- ready, findings, signed plan_token, expires_at
       -> EXECUTE: POST /cloud/templates/images with the same build fields,
            execute=true, preflight_plan_token=<returned token>
  -> proxbox-api: download image -> create VM -> write cicustom user-data snippet
       on <vm_storage>:snippets -> qm template -> returns vmid
  -> PackerBuild.result_template_id=vmid, build_status=success, resolved base source saved;
     PackerTemplate records built_at + last successful source and becomes ready only
     when that source still matches its current declared pin (otherwise stale)
```

Configuration lives on the singleton `PackerPluginSettings`: `proxbox_api_url`
plus a Fernet-encrypted `proxbox_api_key_encrypted` (`set_proxbox_api_key()` /
`get_proxbox_api_key()`), and the File Server package-read username plus
Fernet-encrypted token (`set_fileserver_package_read_token()` /
`get_fileserver_package_read_token()`). Encryption is keyed off
`settings.SECRET_KEY`; there is no `netbox-nms` dependency.

### Dispatch invariants (do not regress)

- Every build trigger must create a `PackerBuild(status="queued")`, set the
  template `build_status` to `"building"`, then immediately call the shared
  `dispatch_build(build)` helper. Creating the row is not enough; no signal
  auto-starts a build.
- `dispatch_build()` MUST enqueue with `PackerBuildJob.enqueue(build_id=build.pk)`
  and **never** pass `instance=build`. `PackerBuild` is not a jobs-assignable
  object type, so `instance=build` raises *"Jobs cannot be assigned to this
  object type"* and the UI Build button silently no-ops.
- If enqueue fails, `dispatch_build()` marks that `PackerBuild` as `failed`,
  appends an error line to the build log, and sets the template back to
  `failed` unless another build is still queued/running. UI and API callers must
  surface the failure instead of reporting a false queued success.
- Auto-rebuild staleness scans follow the same dispatch invariant. They include pin
  drift when `max_age_days` is unset, recover queued rows left by the old
  create-without-dispatch path, set the template to `building`, and dispatch only
  after an optional branching merge succeeds.
- Local `packer init` / `packer build` subprocesses must honor
  `PACKER_BUILD_TIMEOUT_SECONDS` even when the process emits no stdout. The
  watchdog in `_run_subprocess()` is intentionally independent of output
  arrival.
- `target_node` MUST collapse an unset value to `None`, never `""` — proxbox-api
  rejects an empty `target_node` with HTTP 422 (`min_length=1`).
- Executable cloud-config builds MUST use plan → signed preflight → execute.
  `recipe_digest` comes only from the server-rendered non-executing plan, and
  every build field remains identical when the returned, unexpired plan token
  is submitted for execution. Preflight failures/findings, missing or expired
  tokens, plan rejection, and responses without confirmed execution plus final
  artifact verification fail the build and remain visible in its log.
- Cloud-config dispatch resolves the selected primary/build-target URL to
  exactly one netbox-proxbox `ProxmoxEndpoint` by normalized host and port. It
  requires `enabled`, `allow_writes`, and the separate default-off
  `allow_packer_template_builds` capability before queueing and repeats the
  exact resolution immediately before `call_proxbox_build()`. Missing,
  malformed, disabled, unmatched, ambiguous, or revoked endpoints fail closed;
  caller-supplied numeric `endpoint_id` overrides never grant authorization.
  The configured `proxbox_api_url` must match exactly one usable
  netbox-proxbox `FastAPIEndpoint` context, and the backend-id resolver must
  succeed through that context. No configured target rows retains the legacy
  primary endpoint candidate; configured-but-all-disabled or exhausted target
  rows fail closed for cloud builds instead of falling back.
- These contracts are locked by `tests/test_cloud_config_build_static.py` and
  `tests/test_build_dispatch_behavior.py`.

### Prerequisites (proxbox-api side)

- `proxbox-api >= 0.0.20` and `netbox-proxbox >= 0.0.25` with the explicit
  packer-template capability bound into the signed-preflight contract,
  `PROXBOX_ENABLE_CLOUD_IMAGE_EXECUTION=true`, and `PROXBOX_SSH_KEY_DIR`; the
  runtime image bakes in `openssh-client` (`0.0.18.post1`). The target
  `ProxmoxEndpoint` needs `allow_writes=True` and
  `allow_packer_template_builds=True`, and the chosen storage must allow
  `snippets,import,images` content types. A 404 from the preflight endpoint is
  deliberately incompatible and fails closed. `proxbox-api 0.0.19.post5`
  predates the narrow server-side gate; never fall back to the legacy
  one-step execute call.
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
| `install_nms_agent` | bool | `False` | Injects the pinned static NMS host agent, root-only config, and systemd unit. Injection is skipped only when all three managed paths and the exact bootstrap command are already present; partial state is completed. Default-off preserves every existing template; the Akvorado seed opts in. |
| `nms_agent_backend_url` | URL | `"https://backend.nms.nmulti.cloud"` | Bootstrap, heartbeat, and OTLP base URL. Model/form/API validation and build rendering require HTTPS; rendering also rejects credentials, query strings, and fragments. |

The injection is **idempotent** — running the same template twice produces the
same cloud-config. The seeded Zabbix 7.4 template already has
`"zabbix-agent2"` in its content, so the Zabbix injection is skipped for it.
The seeded InfluxDB template already has `qemu-guest-agent` in its packages
list, so only the `systemctl enable` runcmd line is added.

The template form exposes the optional NMS agent toggle and backend URL next to
the existing QEMU/Zabbix controls. The NMS agent remains disabled unless a
template opts in.

Migration `0008_packertemplate_monitoring_agents.py` adds the QEMU/Zabbix
fields. Migration `0023_packertemplate_nms_agent_and_service_marker.py` adds
the NMS agent fields and read-only `provisions_service` marker.

**Password SSH (`ssh_pwauth`).** `_inject_monitoring_agents()` also always sets
`ssh_pwauth: true` in the baked `#cloud-config` (idempotent — skipped only if
the template's own YAML already declares `ssh_pwauth`, so an explicit
`ssh_pwauth: false` is honored). This is intentionally NOT a model field: every
cloud-init template must accept username+password SSH so a customer can log in
with the credentials entered at VM-creation time. It only *permits* password
auth in the guest sshd — key-based SSH stays the default — and no password is
ever baked into the golden image: the per-VM password is supplied at clone time
via Proxmox cloud-init `cipassword` (proxbox-api `CloudInitPayload.password`,
emersonfelipesp/proxbox-api#218).

## Packer Template Table Create-Instance Modal

The `/plugins/packer/templates/` table includes a custom
`PackerTemplateTable.create_instance` `TemplateColumn` rendered by
`netbox_packer/inc/create_instance_button.html`. Each row opens a Bootstrap
modal scoped to the selected `PackerTemplate` and posts to the model view
registered as `packertemplate_create_instance`
(`PackerTemplateCreateInstanceView`, path `create-instance/`).

The modal gathers:

- proxbox-api backend `ProxmoxEndpoint` ID (required);
- destination VMID and VM name;
- target node, storage, CPU, memory, clone/start toggles;
- optional cloud-init user, SSH keys, static network, and DNS servers.

`PackerTemplateCreateInstanceForm` validates the POST and builds the exact
`/cloud/vm/provision` payload. `call_proxbox_vm_provision()` sends that payload
to the configured `PackerPluginSettings.proxbox_api_url` using the encrypted API
key. The selected template supplies `template_vmid` from
`proxmox_template_id`, plus default `target_node` and `storage`.

Important boundary: the create-instance modal's numeric proxbox-api endpoint id
is a provisioning selector, not template-bake authorization. Cloud-config bakes
instead match `PackerTemplate.proxmox_endpoint` or an enabled
`PackerBuildTarget.proxmox_endpoint` URL to exactly one netbox-proxbox endpoint,
verify both write gates, and use netbox-proxbox's established
`resolve_backend_endpoint_id()` translation against the exact configured
backend. Never trust a caller-supplied numeric build override as that proof.
If any target rows are configured, cloud builds must select an enabled,
available target; an all-disabled or exhausted set is an authorization failure,
not permission to use the template's primary endpoint.

### Seeded examples and migration chain

All seed migrations use `get_or_create` for idempotency. Historical seed
reverse functions are no-ops to avoid deleting operator data on rollback;
new reversible seeds such as `0013` delete only the named rows they add.

| Migration | Template name | VMID | OS | ProxmoxEndpoint | Notes |
|---|---|---|---|---|---|
| `0006` | `zabbix-7.4-ubuntu-2604-pgsql-nginx` | 9010 | Ubuntu 26.04 | `https://10.0.30.139:8006` (dev) | Zabbix 7.4 + PostgreSQL + nginx; dev host only |
| `0007` | `influxdb-2-ubuntu-2404-proxmox-collector` | 9011 | Ubuntu 24.04 | `https://10.0.30.139:8006` (historical dev endpoint) | Immutable historical seed; additive migration `0020` replaces the row's credential-generating content and marks it pending; development only |
| `0008` | *(schema only — adds monitoring-agent fields)* | — | — | — | Adds `install_qemu_guest_agent`, `install_zabbix_agent2`, `zabbix_server` to `PackerTemplate` |
| `0009` | `k8s-1.31-ubuntu-2404-node` | 9012 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Kubernetes 1.31 base node (containerd + kubelet/kubeadm/kubectl, pre-pulls CP images) |
| `0010` | *(schema only — adds RegexValidator to `zabbix_server` field)* | — | — | — | `AlterField` on `PackerTemplate.zabbix_server`; no data changes |
| `0011` | `k8s-1.31-control-plane-ubuntu-2404` | 9013 | Ubuntu 24.04 | `https://10.0.30.71:8006` | K8s 1.31 control-plane (pre-pulls all CP images for fast `kubeadm init`) |
| `0011` | `k8s-1.31-worker-node-ubuntu-2404` | 9014 | Ubuntu 24.04 | `https://10.0.30.71:8006` | K8s 1.31 worker (no CP image pre-pull) |
| `0012` | `pdns-auth-ubuntu-2404` | 9017 | Ubuntu 24.04 | `https://10.0.30.71:8006` | PowerDNS Authoritative 4.9 + SQLite3 backend + REST API on 8081; DNS domain `nmulti.cloud`, nameservers `168.0.96.26`/`168.0.96.27` |
| `0012` | `pdns-recursor-ubuntu-2404` | 9018 | Ubuntu 24.04 | `https://10.0.30.71:8006` | PowerDNS Recursor 5.1 caching forwarder → `168.0.96.26`/`168.0.96.27`; allows RFC1918 clients |
| `0013` | `powerdns-auth-recursor-ubuntu` | 9019 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Co-hosted PowerDNS Authoritative + Recursor; auth on `127.0.0.1:5300`, recursor on primary interface `:53`, private client ranges only |
| `0014` | `tpl-fileserver-allinone-ubuntu-2404` | 9032 | Ubuntu 24.04 | `https://10.0.30.71:8006` | File Server all-in-one; Samba AD/DC packages + Nextcloud prerequisites + pip-installed `nms-fileserver-agent`; runtime provisioning supplies tenant state and enrollment token |
| `0015` | `passbolt-ce-ubuntu-2404` | 9060 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Passbolt CE native `passbolt-ce-server` (nginx + php-fpm + local MariaDB) for `credential.nmulti.cloud` on node `10.0.30.71`; `PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED=true`, TLS upstream (HTTP :80); DB password generated first-boot, server key/JWT/DB from data migration |
| `0016` | `ubuntu-2204-cloudinit-base` | 9040 | Ubuntu 22.04 | `https://10.0.30.71:8006` | Base Ubuntu LTS cloud-init template for the customer VM catalog; minimal `#cloud-config` (QGA + Zabbix + `ssh_pwauth` injected at build time). Shares installer config `ubuntu-lts-base-cloud-config` |
| `0016` | `ubuntu-2404-cloudinit-base` | 9041 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Base Ubuntu LTS cloud-init template (see above) |
| `0016` | `ubuntu-2604-cloudinit-base` | 9042 | Ubuntu 26.04 | `https://10.0.30.71:8006` | Base Ubuntu LTS cloud-init template (see above). Verify the 26.04 cloud image URL resolves before baking |
| `0017` | `tpl-fileserver-allinone-ubuntu-2404` | 9300 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Repoints the File Server template to installer config v1.0.1, corrects its current VMID to 9300, injects the authenticated package-Read index, and marks it pending for rebake |
| `0018` | *(schema only — `AlterField` on `PackerTemplate.name`)* | — | — | — | Adds a DB-level `unique=True` constraint to `name`. Historical/defense-in-depth: at the time this migration landed, the File Server package-index credential guard in `package_index.py` trusted an exact `name` match, so this stopped two rows sharing `FILESERVER_TEMPLATE_NAME` simultaneously. Migration 0019 replaced `name` as the actual credential-injection trust boundary — see below |
| `0019` | *(schema + data — `AddField` + `RunPython` on `PackerTemplate`)* | — | — | — | Adds `is_fileserver_golden_template` (`BooleanField`, `editable=False`) and stamps it `True` on the row named `tpl-fileserver-allinone-ubuntu-2404`. `unique=True` (0018) only stops two rows sharing the trusted name *simultaneously* — it does not stop the trusted row being renamed away and a different row later reclaiming the freed name. `package_index.py` now authorizes credential injection on this immutable flag instead of on `name`; the flag is settable only by a migration (excluded from `PackerTemplateForm` and the DRF serializer's explicit `fields` tuples) |
| `0020` | `influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics` | 9050 | Ubuntu 24.04 | Selected per build | Credential-free, version-pinned OSS 2.9.1 profile for Proxmox metrics/Flux; requires an authorized enabled build-target URL + `target_node` |
| `0020` | `influxdb-core-3.11.0-ubuntu-2404` | 9051 | Ubuntu 24.04 | Selected per build | Credential-free, version-pinned Core 3.11.0 profile; requires an authorized enabled build-target URL + `target_node` |
| `0021` | *(schema only — `AddField` on `PackerPluginSettings`)* | — | — | — | Adds the plaintext File Server package-read username and Fernet-encrypted token; build rendering and redaction source both from this singleton instead of worker environment variables |
| `0022` | `fileserver-allinone-cloud-config` | 9300 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Replaces the stale environment-variable rotation comment in the existing v1.0.1 installer config with `PackerPluginSettings` / `set_fileserver_package_read_token()` guidance, updates its checksum, and marks linked templates pending for rebake |
| `0023` | *(schema only — NMS agent + service marker)* | — | — | — | Adds optional `install_nms_agent` (default `False`), `nms_agent_backend_url`, and non-editable `provisions_service` fields |
| `0024` | `akvorado-2.4.0-ubuntu-2404` | 9070 | Ubuntu 24.04 | `https://10.0.30.71:8006` | Akvorado 2.4.0 all-in-one Compose image with Kafka 4.2.0, Valkey 9.0, ClickHouse 26.3, exact lifecycle unit `akvorado.service`, working default config, and NMS agent self-registration enabled |
| `0026` | *(data only — hardens the `0020` profiles)* | 9050/9051/9011 | — | — | Brings both `0020` InfluxDB profiles and the legacy `9011` row to `0025` parity: single-key repository trust, final-release-only version pin, bounded downloads and readiness loop. Locked compare-and-set write, so a concurrent operator edit is never overwritten; a row that no longer matches the exact `0020` baseline **fails the migration by name** rather than being skipped; rebake invalidation follows `installer_config_id`; refuses to run while a build is queued/running; reverse is a no-op |
| `0027` | *(schema only — base image pin)* | — | — | — | Adds optional `base_image_url` + `base_image_sha256` to `PackerTemplate`. A pinned URL without a digest fails the build closed; the digest is forwarded to proxbox-api as `sha256`. Defaults empty, so existing templates are unchanged |
| `0028` | *(schema only — base image build snapshots)* | — | — | — | Records the resolved URL + digest on each successful cloud-image build and on the template as its last successful source. Desired-vs-built pin drift is stale even without an age policy; snapshot fields are machine-managed |
| `0029` | `influxdb-core-3.11.0-debian-13` | 9052 | Debian 13 | Selected per build | Pins the one Debian 13 profile to the **dated** snapshot `trixie/20260509-2473/debian-13-genericcloud-amd64-20260509-2473.qcow2` and its verified sha256. Debian publishes only SHA512SUMS, so the digest was produced by downloading the artifact, matching its SHA-512 to the published value, then hashing for SHA-256. No GPG signature exists in that directory, so trust is TLS + published checksum. Compare-and-set against the unpinned state; refuses to overwrite an operator's own pin; reverse is a no-op |
| `0030` | `influxdb3-explorer-1.9.0-debian-13` | 9053 | Debian 13 | Selected per build | Credential-free Explorer UI in `influxdata/influxdb3-ui`, pinned to the reviewed 1.9.0 multi-architecture manifest digest. Debian `docker.io`, loopback-default `:8080`, lifecycle owned by `influxdb3-explorer.service`; Core token supplied only after clone through the `nms-secret:` provision-time boundary |
| `0032` | *(data only — corrects endpoint-authorization guidance)* | 9050–9053 | — | — | Compare-and-set update for the four endpoint-agnostic InfluxDB template descriptions: replaces obsolete caller `endpoint_id` instructions with authorized enabled `PackerBuildTarget` URL + `target_node` guidance. Missing, renamed, already-corrected, and operator-edited rows remain untouched; reverse is a no-op |
| `0025` | `influxdb-core-3.11.0-debian-13` | 9052 | Debian 13 | Selected per build | InfluxDB 3 Core 3.11.0 on Debian 13 with the production posture baked in: managed config on `127.0.0.1:8181` with token auth enabled, telemetry off, Processing Engine off, `influxdb3-core.service` drop-in, held package, `node-id` from the per-VM SMBIOS UUID. Credential-free; Zabbix/NMS agent injection off (Ubuntu/amd64-only injectors); refuses any non-Debian-13 release |

#### Migration 0020 — InfluxDB profiles

The two current InfluxDB profiles have no baked endpoint, user, password,
token, organization, bucket, or database. Their cloud-init verifies the
InfluxData signing-key fingerprint, selects only the requested semantic patch
version from APT, verifies the installed version, and applies `apt-mark hold`.
Build requests select an authorized enabled build-target URL and `target_node`,
with optional `template_vmid` and `storage` selectors. netbox-packer resolves
the exact proxbox-api endpoint id and never forwards a legacy `ssh_host`, so
proxbox-api derives transport from the selected persisted endpoint. Product bootstrap,
database/bucket creation, tokens, configs, files, services, health, and journal
operations are performed through typed NMS RPC; plaintext credentials are
stored only by the netbox-nms secret bridge and RPC results contain
`nms-secret:` references.

#### Migration 0008 — monitoring-agent fields

Adds three fields to `PackerTemplate` used by `_inject_monitoring_agents()` at build time:

- `install_qemu_guest_agent` (BooleanField, default `True`) — injects `qemu-guest-agent` install + `systemctl enable`.
- `install_zabbix_agent2` (BooleanField, default `True`) — injects Zabbix Agent 2 bootstrap. **Injection is skipped entirely** if the installer config already contains the string `"zabbix-agent2"` (hyphen).
- `zabbix_server` (CharField, default `"zabbix.nmulti.cloud"`) — sets the `ServerActive=` directive in the injected Zabbix config.

#### Migrations 0023/0024 — Akvorado and NMS host agent

Migration `0023` adds `install_nms_agent` with a deliberately false default,
the production-default `nms_agent_backend_url`, and the migration-managed
`provisions_service` marker. Injection builds a static agent from exact commit
`cec1c4c73d8cf301654ecce63e09c3195fd1b8bb` using a SHA256-verified Go
toolchain. It writes no token or backend signing key and relies on the existing
secure-prefix bootstrap flow. When `provisions_service == "akvorado"`, the
agent's local RPC allowlist contains exactly `akvorado.service`; its own Zabbix
management stays disabled because the existing Zabbix injector owns that
configuration.

Migration `0024` seeds `akvorado-2.4.0-ubuntu-2404` at VMID `9070` on
CLUSTER01-DC01 (`https://10.0.30.71:8006` / `10.0.30.71`). The verbatim source
is `netbox_packer/seeds/akvorado-2.4.0-ubuntu-2404.cloud-config.yaml`. It pins
Kafka `4.2.0`, Valkey `9.0`, ClickHouse `26.3`, and every Akvorado component to
`2.4.0`; no `latest` image is allowed. The single systemd lifecycle owner must
remain named exactly `akvorado.service` and operate
`/opt/akvorado/docker-compose.yml`. The seed includes the default Akvorado
configuration needed for a clean first boot; do not reintroduce a required
post-boot config-deploy step. Because Akvorado trusts a proxy-provided identity
header instead of authenticating users itself, the console is bound only to
`127.0.0.1:8081`; access requires an SSH tunnel or a separately provisioned
authenticating reverse proxy.

Created VMs already retain `source_packer_template`. Downstream hooks follow
that lineage to `provisions_service="akvorado"`; do not replace this with
hostname inference or duplicate it as a second VM tag. Keep Kafka/Valkey/
ClickHouse/Akvorado versions, the unit name, VMID, endpoint, docs, and tests
aligned whenever this seed changes.

**Pins are cloud-config-only.** `PackerTemplate.clean()` rejects `base_image_url` /
`base_image_sha256` unless the installer config is `cloud_config`, and `is_stale` ignores a
pin on any other type. Only the cloud-config builder resolves a base image, forwards the
digest for verification, and records the at-build snapshot; the local Packer path does
none of those, so a pin there would be silently unenforced and would also make the
template permanently stale — an endless rebuild loop under `auto_rebuild`. The predicate is
`base_image.base_image_pin_applies()`.

#### Migration 0029 — the one pinned profile

`0027` added the pin fields and `0028` their at-build snapshots, but no profile used
them, so the mechanism protected nothing. `0029` pins exactly one:
`influxdb-core-3.11.0-debian-13` (VMID 9052).

The migration fails closed if that exact template row is missing, and its
compare-and-set must update exactly one row. A rename, deletion, or concurrent edit
cannot let migration `0029` be recorded as a zero-effect success.

**Only this profile, deliberately.** Pinning a template that is already `ready` marks it
pending for rebake (`pin_differs_from_built_source`), so pinning the whole seeded catalog
would demand estate-wide rebakes — an operator decision. 9052 has no baked artifact, so
pinning it invalidates nothing.

**Obtaining a digest (the required procedure).** Debian publishes **only `SHA512SUMS`**
for cloud images — there is no `SHA256SUMS`, and SHA-256 cannot be derived from SHA-512.
So: fetch `SHA512SUMS` from the dated snapshot directory, download the exact dated
`.qcow2`, confirm its SHA-512 equals the published value, and only then compute the
SHA-256. Step 3 is what makes the digest mean anything — it proves the hashed bytes are
the bytes Debian published a checksum for. A checksum copied from a listing without
hashing the artifact proves only that the listing and the field agree.

**Honest limit:** the snapshot directory carries no `SHA512SUMS.sign`, so this chain is
TLS to `cloud.debian.org` plus the published checksum — better than an unpinned mutable
URL, weaker than an offline-verifiable Debian signature. Do not describe it as
signature-verified.

Refreshing the pin means repeating the whole procedure against the new snapshot, never
editing the constants alone. Every other seeded profile remains unpinned; that gap closes
per profile, by an operator who has verified a digest.

#### Migration 0027 — base image pin

Adds `base_image_url` and `base_image_sha256` to `PackerTemplate`, both optional and
empty by default so every existing template keeps its behaviour and payload.
`jobs._resolve_cloud_image_source()` resolves URL and digest with precedence
override → template field → derived release default (URL only), and forwards the digest
to proxbox-api as `sha256` (a field its `CloudImageTemplateBuildRequest` already
accepts), omitting it entirely when empty.

Base-image URLs must be ordinary artifact URLs with no userinfo, query string,
or fragment. This is parsed structurally at API/model boundaries and rechecked by
the build job so signed-download query credentials cannot enter persisted build
state. Logs, proxbox output, and successful-build provenance use a defensively
redacted URL. If authenticated image downloads are added later, use an opaque
secret reference rather than inline URL credentials.

**A pinned URL must carry a digest** — supplied by either source — or the build fails
closed. An unverified pin looks like provenance while guaranteeing nothing about the
bytes. A malformed digest is refused rather than forwarded; an uppercase one is
normalised, since hex is case-insensitive. An **unpinned** release build still works
without a digest, because requiring one everywhere would break every existing template
at once; that gap closes per profile, by pinning it.

**No profile is pinned by this migration.** Choosing a dated vendor image and obtaining
a *verified* digest are operator judgements — see
`docs/cloud-init-template-images.md` → "Base Image Pinning" for the procedure, including
the requirement to record where each digest came from and how it was verified.

#### Migration 0028 — successful-build base image snapshots

Adds machine-managed `base_image_url_at_build` and
`base_image_sha256_at_build` fields to `PackerBuild` and `PackerTemplate`. A successful
cloud-image build records the resolved source on its build row and updates the
template's last-successful source in the same locked transaction as `built_at`.
The template becomes `ready` only when its current declared pin still matches the
resolved source; an override mismatch or a pin edited during the build is committed as
`stale`. `PackerTemplate.is_stale` compares a declared pin with those snapshots,
so changing or clearing a pin, or using a per-build pin that differs from the template,
cannot silently describe old bytes as current. Pin/checksum staleness is evaluated even
when `max_age_days` is unset. Existing unpinned rows with empty historical snapshots
retain their prior behavior.

#### Migration 0025 — InfluxDB 3 Core on Debian 13

Seeds `influxdb-core-3.11.0-debian-13` (VMID `9052`) with installer config
`influxdb-core-3.11.0-debian-13-cloud-config` v`3.11.0`, `os_family="debian"`,
`os_version="13"`. Endpoint-agnostic like the `0020` profiles
(`proxmox_endpoint=""`, `proxmox_node="select-at-build"`), so build dispatch
selects an authorized enabled build-target URL and `target_node`. The verbatim cloud-config lives at
`netbox_packer/seeds/influxdb-core-3.11.0-debian-13.cloud-config.yaml`, and the
migration constant must stay byte-identical to it (asserted by
`tests/test_cloud_config_build_static.py::test_influxdb3_core_debian13_seed_contract`).

The difference from the Ubuntu Core 3 profile (`9051`) is that this one bakes a
**production posture** instead of package defaults:

- managed `/etc/influxdb3/influxdb3-core.conf` (`root:influxdb3`, `0640`) with
  `object-store = "file"`, explicit `data-dir`, `http-bind = "127.0.0.1:8181"`,
  `log-filter`, `wal-flush-interval`, and `disable-telemetry-upload = true`;
- **`plugin-dir` deliberately omitted** — the Python Processing Engine stays off;
- `influxdb3-core.service` drop-in `20-production.conf` with
  `Restart=on-failure`, `RestartSec=5s`, `TimeoutStopSec=120s`;
- `node-id` derived at first boot from the **per-VM SMBIOS UUID**
  (`/sys/class/dmi/id/product_uuid`), falling back to the per-instance
  `/etc/machine-id`, and failing closed if neither is readable. **Not** the
  hostname: the Proxmox clone pipeline reuses this template's own cicustom
  meta-data (its `local-hostname` is a fixed placeholder) and clone provisioning
  changes only the Proxmox VM name, so every standard clone can boot with the same
  hostname — deriving the node id from it would hand every clone the same InfluxDB
  node identity;
- Debian-13-only gate on `/etc/os-release` plus `amd64`/`arm64` and systemd
  checks, exiting non-zero rather than half-configuring another platform;
- a **single-key** trust boundary: the downloaded key is imported into an isolated
  `GNUPGHOME` and only the expected primary fingerprint is exported
  (`--export-options export-minimal`), with the exported keyring then asserted to
  hold exactly one `pub` key with that fingerprint. Checking that the downloaded file
  merely *contains* the fingerprint and dearmoring all of it would also trust an
  attacker key bundled alongside the genuine one, which could sign repository
  metadata and gain root during install;
- an `apt-cache madison` pin to a **final** `3.11.0` (any `~` prerelease is refused
  in both candidate selection and the post-install `dpkg-query` check),
  `apt-mark hold`, and a genuinely bounded
  wait on the unauthenticated `http://127.0.0.1:8181/ready` endpoint — each probe
  carries `--connect-timeout`/`--max-time` and the loop enforces an overall
  deadline, so a socket that accepts but never answers cannot hang
  once-per-instance cloud-init after a partial install.

**Monitoring injection is deliberately partial for this template.**
`install_qemu_guest_agent` is on (a plain Debian package), but
`install_zabbix_agent2` and `install_nms_agent` are **off**, and that is a
platform constraint rather than a preference: `_inject_monitoring_agents()` builds
the Zabbix repository package name as `ubuntu${VERSION_ID}`, which is a
nonexistent `ubuntu13` package on Debian 13, and the NMS agent bootstrap
hard-requires `amd64` while this image also declares `arm64`. Enabling either
would produce a cloud-config that fails on the very platform the template
declares. Turn them on only once those injectors are OS-family- and
architecture-aware.

That choice also makes this seed's installer the **last** `runcmd` entry, which
matters: cloud-init shellifies `runcmd` into a plain `/bin/sh` script with no
`set -e`, so a failing non-final command is masked by a later command's success.
`test_influxdb3_core_debian13_injected_cloud_config_stays_debian_safe` asserts
both properties against the **fully injected** config, not the pristine seed.

**Base image resolution was fixed for this seed.**
`jobs._resolve_cloud_image_url()` previously returned the Bookworm Debian 12 image
for *every* `os_family="debian"` row, ignoring `os_version`. It now resolves the
release codename from `os_version` via `_DEBIAN_CODENAMES`
(`11`→bullseye, `12`→bookworm, `13`→trixie), raises for an unknown release
instead of falling back, and keeps the Debian 12 default only for a row with no
version. Without this the Debian 13 template would have baked on a Debian 12
image; because a `cloud_config` bake never executes cloud-init, that unusable
artifact could still be marked `ready` and would only fail its own OS gate later,
at clone time. Keep `_DEBIAN_CODENAMES` in step with
`choices.OS_VERSIONS_BY_FAMILY[debian]`.

Seeding uses `0024`'s collision guard: `get_or_create` plus a `RuntimeError`
listing mismatched fields, so a pre-existing row is reported and left untouched
rather than overwritten. The reverse function is intentionally a no-op because an
operator may already have baked the VMID.

**Credential-free, deliberately.** No admin token, TLS material, or per-bake
state is written. The first administrative token is created and vaulted only by
`service.influxdb.1.bootstrap` (`family="core3"`) through typed NMS RPC, which
returns an `nms-secret:` reference. Do not add token generation here. Token
authentication remains enabled in the guest, which is why the bind stays on
loopback — a remote listener would expose bearer tokens over plaintext HTTP; put
a TLS reverse proxy in front, or use the audited RPC installer with TLS material.

For hosts that already exist, `netbox-rpc` seeds
`os.linux.debian.13.preflight_influxdb3_core` (read, no approval) and
`os.linux.debian.13.install_influxdb3_core` (write, approval required), which
apply the same posture over audited SSH and accept the operator installer's
parameters. `netbox-packer` must not import or depend on `netbox-rpc`; those
procedure names appear here as documentation only.

#### Migration 0030 — InfluxDB 3 Explorer on Debian 13

Seeds `influxdb3-explorer-1.9.0-debian-13` (VMID `9053`) with installer config
`influxdb3-explorer-1.9.0-debian-13-cloud-config` v`1.9.0`,
`os_family="debian"`, and `os_version="13"`. It is endpoint-agnostic
(`proxmox_endpoint=""`, `proxmox_node="select-at-build"`), so build dispatch
selects an authorized enabled build-target URL and `target_node`. The tracked cloud-config is
`netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml` and
must remain byte-identical to the migration constant.

Docker comes only from Debian's signed repository (`docker.io`). Both pull and
runtime use exactly
`influxdata/influxdb3-ui@sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc`.
The single lifecycle owner is `influxdb3-explorer.service`; it mounts persistent
state from `/var/lib/influxdb3-explorer` at `/db` and provisioned configuration
from `/etc/influxdb3-explorer` read-only at `/app-root/config`. Container port
`8080` is published on the configurable `EXPLORER_HOST_BIND`, whose default is
`127.0.0.1`. Anyone who can reach Explorer inherits its configured Core token's
permissions, so any non-loopback bind requires an explicit access-control design.
Explorer 1.9.0 runs as non-root uid/gid `1500`, so `/db` must stay
`1500:1500` mode `0700`; the config directory stays `root:1500` mode `0750` and
provisioned `config.json` is mode `0640`.

The seed is entirely credential-free. It contains no Core URL, token, password,
TLS private key, session secret, or environment-specific secret reference. After
clone, `service.influxdb.1.token_create` mints and vaults the Core token and
returns `nms-secret:<opaque-id>`; provision-time automation resolves the reference
only in memory, writes `root:1500` mode-`0640`
`/etc/influxdb3-explorer/config.json`, and restarts the unit. Never move that
per-instance step into cloud-init.

The credential-free contract is enforced on the **fully injected** YAML, not
only on the tracked seed. Immediately before `call_proxbox_build()`, any template
whose immutable `provisions_service` marker is `influxdb3-explorer` is rejected
if the final payload contains a Core endpoint/config file, credential-bearing
key or value, private key, encoded `write_files` content that cannot be inspected,
or a non-placeholder `nms-secret:` reference.

Those content rules are a denylist, and a denylist over operator-editable content only
refuses the shapes it enumerates — review found two bypasses of exactly that kind, a
`content: !!binary` scalar (loaded as `bytes` and skipped by a string-only scan) and
`/etc/influxdb3-explorer/./config.json` (an alias an exact path comparison did not
recognise). The guard therefore **ends with a write-path allowlist**: every `write_files`
path is canonicalised with `posixpath.normpath` and must be one of the five files the
image legitimately writes, unknown entry keys and non-text content are refused outright,
and binary anywhere in the payload fails closed. The specific content diagnostics run
first so they still produce the actionable message when they apply; the allowlist is the
catch-all that makes a newly invented carrier fail closed instead of waiting to be
enumerated. Rejection is logged and proxbox-api
is never called with the tainted payload. Keep this runtime boundary alongside
the static seed assertions whenever injection or rendering changes.

First boot refuses non-Debian-13 and unsupported architectures, bounds apt and
the digest-addressed image pull, applies a maximum accepted image size, and uses
a bounded local readiness loop. The installer runs under `set -Eeuo pipefail`
with exactly one `EXIT` trap, converts `TERM`/`INT`/`HUP` to non-zero exits, and
writes `/var/lib/nms/influxdb-install-failed` on failure. Zabbix and NMS agent
injection are off for the same Debian/arm64 constraints as VMID 9052; the fully
injected config must keep this installer last in `runcmd`. Seeding uses the 0025
collision guard, excludes mutable build state, and reverses as a no-op.

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

#### Migration 0013 — Co-hosted PowerDNS Authoritative + Recursor

Seeds `powerdns-auth-recursor-ubuntu` (VMID 9019) on ProxmoxEndpoint
`https://10.0.30.71:8006` / node `10.0.30.71`. This is the CLUSTER01-DC01 PVE
cluster host default; the node and VMID may still be overridden at build
dispatch when an operator needs a different bake target.

The cloud-config installs `pdns-server`, `pdns-backend-sqlite3`,
`pdns-recursor`, `qemu-guest-agent`, `sqlite3`, and `iproute2` from the Ubuntu
24.04 package repositories. It initializes the bundled `gsqlite3` schema under
`/var/lib/powerdns/pdns.sqlite3`.

Authoritative is private to the VM:
- `local-address=127.0.0.1`
- `local-port=5300`
- REST API webserver bound to `127.0.0.1:8081`
- `api-key` populated from the placeholder variable `PDNS_AUTH_API_KEY`

Recursor is the public-facing resolver surface:
- `local-address` is set to the VM primary IPv4 discovered at first boot
- `local-port=53`
- `forward-zones` defaults local zones to `nmulti.cloud=127.0.0.1:5300`
- optional `PDNS_LOCAL_FORWARD_ZONES_RECURSE` appends `forward-zones-recurse`
- REST API webserver bound to `127.0.0.1:8082`
- `allow-from` is restricted to `127.0.0.1/8`, `10.0.0.0/8`,
  `172.16.0.0/12`, `192.168.0.0/16`, and `::1/128`

Never set the recursor allow-list to `0.0.0.0/0`; this seed must not create an
open resolver. Replace `PDNS_AUTH_API_KEY` and `PDNS_RECURSOR_API_KEY`
placeholders before production use.

#### Migration 0014 — File Server all-in-one

Migration 0014 historically seeded `tpl-fileserver-allinone-ubuntu-2404`
(VMID 9032) on ProxmoxEndpoint
`https://10.0.30.71:8006` / node `10.0.30.71`, using installer config
`fileserver-allinone-cloud-config`. Migration 0014 remains the immutable v1.0.0
history. The current verbatim cloud-config source is tracked at
`netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml`.
`tests/test_cloud_config_build_static.py` preserves migration 0017's historical
content while asserting that additive migration 0022 applies the current
settings-based credential-rotation instructions.

The cloud-config installs Samba AD/DC packages, Nextcloud web/PHP
prerequisites, `qemu-guest-agent`, `zabbix-agent2`, and `python3-venv`.
`nms-fileserver-agent` is not an apt package in this image. The bake creates
`/opt/nms-fileserver-agent/venv` and installs
`NMS_FILESERVER_AGENT_PIP_SPEC` (default `nms-fileserver-agent==0.1.0`) from the
N-MultiCloud Gitea PyPI index. The singleton `PackerPluginSettings` row must
provide `fileserver_package_read_user` and a token encrypted through
`set_fileserver_package_read_token()` for a dedicated non-human identity whose
token has only Gitea package-Read permission. Never supply a personal token or
`PACKAGE_WRITE_TOKEN`. The dispatch path fails closed when either setting is
missing, URL-encodes both values, and redacts the raw and encoded token from
persisted build output. Public `httpx` is installed from PyPI first; the pinned
agent is installed with `--no-deps` from the authenticated sole private index in
root-only `/etc/nms-fileserver-agent/pip.conf`. Operators rotate the encrypted
settings token and rebake VMID 9300; every clone otherwise retains the
credential baked into that file. It writes
`/etc/nms-fileserver-agent/config.env` with
`NMS_BACKEND_URL=https://backend.nms.nmulti.cloud` and
`NETBOX_URL=https://netbox.nmulti.cloud`.

Migration `0017_update_fileserver_agent_package_index.py` creates installer
config version `1.0.1`, repoints the existing template row, corrects its current
VMID to 9300, and marks it pending so deployments that already applied migration
0014 receive the new bootstrap.
Migration `0022_update_fileserver_package_settings_comment.py` leaves migration
0017 immutable and replaces only its stale environment-variable rotation prose
in existing v1.0.1 rows with the current `PackerPluginSettings` setter workflow.

The image is software-only: tenant provisioning is deferred to clone-time
automation, no enrollment token is baked, `nginx` is disabled,
`smbd`/`nmbd`/`winbind` are masked, `nms-fileserver-agent-enroll.service` is
installed but disabled/not run on the golden template, and
`nms-fileserver-agent-heartbeat.timer` is also disabled until runtime user-data
supplies the per-instance one-time token and starts the agent lifecycle.

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

## NetBox compatibility: two tiers, one shared module

`netbox_packer/compat.py` is the single declaration of which NetBox releases this plugin
supports, and `PluginConfig.min_version`/`max_version` are sourced from it rather
than re-typed as literals:

- **stable** `4.5.8` – `4.6.99` — admitted silently; specific versions in
  this band are exercised in CI, the rest are admitted on their strength;
- **experimental** `4.7.0` – `4.7.99` — loads and runs with no configuration
  change, and emits system check `netbox_packer.W001` (a **Warning**, never an Error)
  plus one `ready()` log line. A version that cannot be classified reports
  `netbox_packer.W002` rather than passing silently. Operators silence the notice with the
  `silence_netbox_compatibility_warning` key in this plugin's
  `PLUGINS_CONFIG` entry. NetBox does **not** read
  `SILENCED_SYSTEM_CHECKS` from `configuration.py`, so that route does
  nothing.

**`compat.py` is vendored byte-identically across `netbox-proxbox`,
`netbox-ceph`, `netbox-packer`, `netbox-pbs`, and `netbox-pdm`.** Change it in
one repo and you must change it in all five, bumping `CONTRACT_VERSION` when the
contract itself moves. Verify with
`sha256sum */compat.py` across the five checkouts — the
`proxbox-stack-code-review` skill runs that drift check.

Two hard rules:

1. **No Django import at module scope in `compat.py`.** NetBox imports it while
   `netbox/settings.py` is still executing, so every Django touch lives inside a
   function.
2. **Upgrading to NetBox 4.7 means upgrading the whole plugin family.**
   `settings.py` *catches* `IncompatiblePluginError`, warns, and **skips** the
   offending plugin — NetBox still starts. The failure is therefore silent: the
   plugin's views, API routes and jobs are simply absent, and a health probe
   against NetBox still passes. Verify registration with `apps.is_installed()`
   after any upgrade rather than trusting that NetBox came up.

Beta release strings are why the ceiling is `4.7.99` and not something
pre-release-shaped: `release.yaml` at tag `v4.7.0-beta1` reads `version: "4.7.0"`
with `designation: "beta1"`, and the plugin gate compares against
`RELEASE.version` — the bare `"4.7.0"`. `RELEASE.full_version`
(`"4.7.0-beta1"`) is display only.
