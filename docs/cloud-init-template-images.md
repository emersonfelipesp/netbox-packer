# Cloud-init Template Images

`netbox-packer` can store a cloud-init user-data document in
`PackerInstallerConfig` and bake it into a Proxmox VM template through
`proxbox-api`. The plugin does not run local Packer for this path. It creates a
`PackerBuild`, queues `PackerBuildJob`, and delegates the real Proxmox work to
`POST /cloud/templates/images` on `proxbox-api`.

## Flow

1. Create or select a `PackerInstallerConfig` where
   `installer_type = "cloud_config"`.
2. Create or select a `PackerTemplate` that references the installer config.
3. Trigger the template build from NetBox, the API, or the NMS page at
   `/virtualization/packer`.
4. `PackerBuildJob` resolves the build node, base cloud image URL, storage, and
   SSH host.
5. The client sends the exact build body with `execute=false`; proxbox-api
   renders it and returns the server-authored `recipe_digest`.
6. The client submits that digest and the endpoint, node, VMID, provider, and
   storage target to `POST /cloud/templates/images/preflight` contract `1.0`.
7. Only when preflight reports ready and returns an unexpired signed
   `plan_token`, the client resends the unchanged build fields with
   `execute=true` and `preflight_plan_token` set.
8. `proxbox-api` downloads the base image, creates the VM, writes the cloud-init
   snippet as Proxmox `cicustom` user-data, converts the VM to a template, and
   returns a completed, executed, artifact-verified response.

The `PackerPluginSettings` singleton row must include `proxbox_api_url` and an
encrypted proxbox-api key (see [Configuration](configuration.md) — today this
is set via the Python shell only, not a UI page or REST endpoint). The target
`ProxmoxEndpoint` in netbox-proxbox must allow writes, and the selected Proxmox
storage must support `snippets`, `import`, and `images`.

This requires the signed-preflight contract available in
`proxbox-api >= 0.0.19.post5`. If the preflight endpoint returns 404, the service
is incompatible: netbox-packer fails the build with an upgrade message and does
not fall back to the unsafe legacy one-step execute call. Unreachable/not-ready
preflight, returned findings, missing or expired plan tokens, plan mismatch, and
unverified execution all remain visible in the build log and fail closed.

## Creating a template from the web form

The template add/edit form (`/plugins/packer/templates/add/`) is optimised for
creating cloud-init images:

- **OS family** and **OS version** are both dropdowns. OS version is grouped by
  OS family (Ubuntu / Debian / RHEL / Proxmox) and, with JavaScript enabled,
  narrows to the family you pick. It works without JavaScript too — every
  version stays listed under its optgroup. An existing template whose stored
  version is not in the offered list keeps that value selectable, so editing an
  older template never fails validation.
- **Machine-managed fields are hidden.** `built_at`, `packer_template_ref`,
  `installer_config_checksum_at_build`, `base_image_url_at_build`, and
  `base_image_sha256_at_build` are written by `PackerBuildJob`, so the form no
  longer asks operators to fill them in. They remain available read-only via the
  REST API.
- **Guidance help text** is shown on `os_version`, `proxmox_template_id`,
  `storage_pool`, `cloud_init_ready`, and `installer_config` to make picking the
  right values for a cloud-init bake obvious.

### Adding a new OS version to the dropdown

Offered versions live in a single mapping,
`OS_VERSIONS_BY_FAMILY`, in `netbox_packer/choices.py`. Add a
`("<version>", "<label>")` tuple to the relevant family list — no database
migration is required, because the model field stays a free-form `CharField`
(the REST API keeps accepting any version string for automation).

```python
OS_VERSIONS_BY_FAMILY = {
    OSFamilyChoices.CHOICE_UBUNTU: [
        ("26.04", "Ubuntu 26.04 LTS"),
        ("24.04", "Ubuntu 24.04 LTS (Noble)"),
        # add new Ubuntu releases here
    ],
    # ...
}
```

## Zabbix 7.4 Monitoring Stack Template

Migration `0006_seed_zabbix_cloud_init.py` seeds the Zabbix 7.4 monitoring
server image.

| Field | Value |
| --- | --- |
| Template name | `zabbix-7.4-ubuntu-2604-pgsql-nginx` |
| Installer config | `zabbix-7.4-ubuntu-2604-pgsql-nginx` |
| OS | Ubuntu `26.04` |
| Template VMID | `9010` |
| Proxmox endpoint | `https://10.0.30.139:8006` |
| Proxmox node / SSH host | `10.0.30.139` |
| Storage | `local` |

Guardrail: `https://10.0.30.9:8006` / `10.0.30.9` is the production
`netbox.nmulti.cloud` Proxmox cluster. Do not seed, bake, or retarget this
monitoring-server template there. The seeded build target is the development
endpoint `https://10.0.30.139:8006` only.

The cloud-init payload installs Zabbix Server 7.4, the PHP frontend, and Agent
2 on Ubuntu 26.04, backed by a local PostgreSQL database and nginx (PHP 8.5),
and initializes the Zabbix database schema on first boot. Because the seed
content already contains `"zabbix-agent2"`, the build-time monitoring-agent
injection in `jobs.py` skips adding a second Zabbix Agent 2 install for this
template.

## Kubernetes 1.31 Node Templates

Migrations `0009_seed_kubernetes_cloud_init.py` and
`0011_seed_k8s_role_templates.py` seed three Kubernetes 1.31 node images on
CLUSTER01-DC01.

| Field | Base node | Control plane | Worker |
| --- | --- | --- | --- |
| Template name | `k8s-1.31-ubuntu-2404-node` | `k8s-1.31-control-plane-ubuntu-2404` | `k8s-1.31-worker-node-ubuntu-2404` |
| Template VMID | `9012` | `9013` | `9014` |
| OS | Ubuntu `24.04` | Ubuntu `24.04` | Ubuntu `24.04` |
| Proxmox endpoint | `https://10.0.30.71:8006` | `https://10.0.30.71:8006` | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` | `10.0.30.71` | `10.0.30.71` |
| Storage | `local` | `local` | `local` |

All three cloud-init payloads install `containerd` and
`kubelet`/`kubeadm`/`kubectl` pinned to `1.31`. The base node and control-plane
images additionally run `kubeadm config images pull` to pre-pull the
control-plane container images for a faster `kubeadm init`; the worker image
skips that pre-pull. `qemu-guest-agent` is enabled on all three.

These are pre-staged node images, not a running cluster: an operator still
runs `kubeadm init` on the control-plane clone and `kubeadm join` on worker
clones after provisioning.

## InfluxDB OSS 2 and Core 3 Profiles

Migration `0020_seed_influxdb_profiles.py` seeds two current, endpoint-agnostic
profiles and hardens the legacy collector row without deleting its Proxmox
artifact.

| Profile | Version | VMID | Port | Intended workload |
| --- | --- | --- | --- | --- |
| `influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics` | OSS `2.9.1` | `9050` | `8086` | Proxmox external metrics and Flux |
| `influxdb-core-3.11.0-ubuntu-2404` | Core `3.11.0` | `9051` | `8181` | SQL, InfluxQL, and processing-engine workloads |
| `influxdb-core-3.11.0-debian-13` | Core `3.11.0` | `9052` | `8181` | SQL and InfluxQL on Debian 13, with the production posture baked in |

Both rows store an empty `proxmox_endpoint` and `select-at-build` as the model
placeholder node. Each build request must provide a positive proxbox-api
`variable_overrides.endpoint_id` and a validated
`variable_overrides.target_node`. Optional validated `template_vmid` and
`storage` overrides select the destination identifiers. When an endpoint ID is present,
netbox-packer suppresses all legacy `ssh_host` values so proxbox-api derives
transport from the same selected endpoint it authorizes.

> **Hardened by migration `0026`.** Both profiles below (and the legacy `9011` row)
> originally shared the Debian 13 profile's three defects: a keyring trust boundary that
> admitted any extra key bundled with the genuine one, a version match that accepted `~`
> prereleases as the pinned release, and unbounded downloads and readiness probes that
> could hang first boot indefinitely. `0026` applies the same fixes made in `0025`.
> A row whose content no longer matches the exact `0020` baseline is **not** rewritten
> (that would discard an operator's edit) and **not** silently skipped — the migration
> fails with the offending rows named, so nobody deploys believing the vector was
> removed everywhere. `0026` also refuses to run while a build is queued or running
> against a linked template, and invalidates rebake state by installer-config
> relationship rather than by the editable template name. Both profiles additionally
> record a durable failure marker at `/var/lib/nms/influxdb-install-failed`, because
> cloud-init's `runcmd` wrapper has no `set -e` and the injected Zabbix bootstrap runs
> after the installer, so a failure would otherwise be masked and reported as success.

The cloud-init profiles:

- trust **exactly one** repository key: the downloaded key is imported into an isolated
  `GNUPGHOME` and only fingerprint
  `24C975CBA61A024EE1B631787C3D57159FC2F927` is exported, with the exported keyring then
  asserted to hold a single `pub` record;
- select only **final** APT package versions matching `2.9.1` or `3.11.0` — any `~`
  prerelease is refused in both candidate selection and post-install verification —
  then verify the installed version and apply `apt-mark hold`;
- enable the correct fixed systemd service and wait for `/health` or `/ready` with
  per-probe connect/total timeouts and an overall deadline, so a socket that accepts a
  connection but never answers cannot hang first boot;
- contain no user, password, token, organization, bucket/database, setup API
  request, private key, or endpoint-specific Proxmox data.

After a clone completes cloud-init, use the typed
`service.influxdb.1.bootstrap`, `database_create`, and `token_create` RPC
procedures through NMS or nms-cli. The backend generates/resolves one-time
plaintext only in memory and stores it through netbox-nms as `nms-secret:`
references. Routine config, managed/plugin files, service state, health, and
journal operations use the remaining typed InfluxDB RPC procedures.

The immutable historical `influxdb-2-ubuntu-2404-proxmox-collector` / VMID
`9011` seed remains development-only on `10.0.30.139`. Additive migration
`0020` replaces the database row's former credential-generating installer
content with the safe OSS profile and marks it pending. Existing artifacts are
never deleted automatically.

### InfluxDB 3 Core on Debian 13 (`0025`)

`0025_seed_influxdb3_core_debian13_cloud_init.py` adds
`influxdb-core-3.11.0-debian-13` at VMID `9052`. It shares the endpoint-agnostic
and credential-free contract above and adds the production posture of the
operator installer rather than leaving the server at package defaults.

| Field | Value |
| --- | --- |
| Template name | `influxdb-core-3.11.0-debian-13` |
| Installer config | `influxdb-core-3.11.0-debian-13-cloud-config` version `3.11.0` |
| OS | Debian `13` (Trixie) |
| Template VMID | `9052` |
| Proxmox endpoint / node | selected at build (`endpoint_id` + `target_node`) |
| Storage | `local` |
| Listener | `127.0.0.1:8181` (loopback only) |
| Managed config | `/etc/influxdb3/influxdb3-core.conf` (`root:influxdb3`, `0640`) |
| Data directory | `/var/lib/influxdb3/data` (`influxdb3:influxdb3`, `0750`) |
| Systemd unit | `influxdb3-core.service` + drop-in `20-production.conf` |
| Service marker | `provisions_service = "influxdb3-core"` |

The verbatim cloud-config source of truth is
`netbox_packer/seeds/influxdb-core-3.11.0-debian-13.cloud-config.yaml`; the
migration constant must stay byte-identical to it, which
`tests/test_cloud_config_build_static.py` asserts.

Beyond the shared behaviours listed above, first boot:

- **refuses any release other than Debian 13** — it reads `/etc/os-release` and
  exits non-zero unless `ID=debian` and `VERSION_ID` is `13`, and it also
  requires an `amd64`/`arm64` architecture and systemd as PID 1, rather than
  half-configuring an unverified platform;
- writes the managed configuration with `object-store = "file"`, an explicit
  `data-dir`, `http-bind = "127.0.0.1:8181"`, `log-filter`,
  `wal-flush-interval`, and `disable-telemetry-upload = true`, deliberately
  **omitting `plugin-dir`** so the Python Processing Engine stays disabled;
- installs a `influxdb3-core.service` drop-in with `Restart=on-failure`,
  `RestartSec=5s`, and `TimeoutStopSec=120s`;
- derives `node-id` from the **per-VM SMBIOS UUID**
  (`/sys/class/dmi/id/product_uuid`, falling back to `/etc/machine-id`) and fails
  closed if neither is readable — the hostname is not usable for this, because the
  clone pipeline reuses the template's cicustom meta-data and clones can therefore
  share a hostname;
- holds the package with `apt-mark hold` and waits on the unauthenticated
  `http://127.0.0.1:8181/ready` endpoint with per-probe
  `--connect-timeout`/`--max-time` plus an overall deadline, dumping unit status
  and journal tail before failing the boot script if readiness never arrives.

Two build-path details are specific to this profile:

- **Base image.** `os_version="13"` resolves to the Trixie Debian 13 cloud image.
  The resolver previously returned Bookworm for every Debian row; since a
  `cloud_config` bake never executes cloud-init, that would have produced an
  artifact marked `ready` whose own OS gate fails at clone time.
- **Monitoring injection.** `install_qemu_guest_agent` is on;
  `install_zabbix_agent2` and `install_nms_agent` are **off**, because the shared
  injectors build an Ubuntu Zabbix package name from `VERSION_ID` and the NMS agent
  bootstrap requires amd64. That also leaves this installer as the last `runcmd`
  entry, so its failure is not masked by a later command in cloud-init's
  `set -e`-less wrapper.

Token authentication stays enabled for every data and admin route: the readiness
probe is the only unauthenticated call the image makes. Because a remote listener
would then expose bearer tokens over plaintext HTTP, the baked posture is
loopback-only — put a TLS reverse proxy in front of it, or use the audited RPC
installer (below) with explicit TLS material.

For hosts that **already exist**, do not re-bake: `netbox-rpc` seeds
`os.linux.debian.13.preflight_influxdb3_core` (read) and
`os.linux.debian.13.install_influxdb3_core` (write, approval required), which
apply the same posture over audited SSH and accept the installer's parameters
(`node_id`, `data_dir`, `http_bind`, `tls_cert`/`tls_key`, `enable_plugins`,
`disable_telemetry`, `wal_flush_interval`, `log_filter`, `package_version`,
`hold_package`, `upgrade_package`, `force_reconfigure`,
`allow_plaintext_remote`). Those procedures also create no credential; the
sanctioned onboarding sequence remains `preflight` -> `install` ->
`service.influxdb.1.bootstrap`.

**Known accepted risk (tracked in issue #96).** Like every other seeded profile,
this one resolves the vendor's **mutable `latest`** image directory and passes no
content digest to `proxbox-api`, so a rebuild is not guaranteed to reproduce the same
root filesystem. Pinning a dated image plus a reviewed `sha256` — and failing closed
when a pinned profile lacks one — is deliberately deferred to that issue rather than
half-implemented, because an unverified digest would look like provenance while
proving nothing.

Per the estate destructive-operation guardrail this migration seeds catalog rows
only and builds nothing. Confirm VMID `9052` is free on the destination cluster
before baking, and supersede a bad artifact by baking a new VMID rather than
deleting the previous one.

### InfluxDB 3 Explorer on Debian 13 (`0030`)

`0030_seed_influxdb3_explorer_debian13_cloud_init.py` adds the independent
`influxdb3-explorer-1.9.0-debian-13` template at VMID `9053`. A separate
template gives Core and Explorer separate build runs and artifacts. Like the
Debian Core profile it is endpoint-agnostic, so each build supplies proxbox-api
`endpoint_id` and `target_node`.

| Field | Value |
| --- | --- |
| Template name | `influxdb3-explorer-1.9.0-debian-13` |
| Installer config | `influxdb3-explorer-1.9.0-debian-13-cloud-config` version `1.9.0` |
| OS | Debian `13` (Trixie) |
| Template VMID | `9053` |
| Proxmox endpoint / node | selected at build (`endpoint_id` + `target_node`) |
| Storage | `local` |
| Container image | `influxdata/influxdb3-ui@sha256:7df00684199c4b983b05b109e72e89aa23a0d6a9a9460d6b90cfd70f979023cc` |
| Listener | configurable host IP, default `127.0.0.1:8080` |
| Persistent data | `/var/lib/influxdb3-explorer` mounted at `/db` |
| Provisioned config | `/etc/influxdb3-explorer` mounted read-only at `/app-root/config` |
| Systemd unit | `influxdb3-explorer.service` |
| Service marker | `provisions_service = "influxdb3-explorer"` |

The verbatim cloud-config source of truth is
`netbox_packer/seeds/influxdb3-explorer-1.9.0-debian-13.cloud-config.yaml`;
the migration constant must stay byte-identical to it. First boot refuses a
non-Debian-13 or unsupported-architecture guest, installs `docker.io` only from
Debian's signed repository, pulls the digest-addressed multi-architecture
manifest under an overall deadline, applies a maximum accepted image size,
and starts the container through the single systemd lifecycle owner. The local
readiness probe has per-attempt and overall deadlines. An `EXIT` trap plus
`TERM`/`INT`/`HUP` conversion records failures durably at
`/var/lib/nms/influxdb-install-failed`.

Explorer 1.9.0 runs as non-root uid/gid `1500`. The installer therefore creates
the writable `/var/lib/influxdb3-explorer` data directory as `1500:1500` mode
`0700`, while `/etc/influxdb3-explorer` stays `root:1500` mode `0750` and is
mounted read-only into the container. Provisioned `config.json` is mode `0640`.

The default bind in `/etc/default/influxdb3-explorer` is loopback. An operator
may replace it with a specific host IP, but Explorer does not provide a second
user-authentication boundary around a configured InfluxDB connection: anyone
who can reach Explorer inherits that connection token's permissions. Remote
access therefore needs an explicit access-control design, normally an
authenticating TLS reverse proxy.

The golden image deliberately starts with no InfluxDB connection. After clone,
`service.influxdb.1.token_create` mints and vaults the Core token and returns
only an `nms-secret:<opaque-id>` reference. Provision-time automation resolves
that reference only in memory, writes `root:1500` mode-`0640`
`/etc/influxdb3-explorer/config.json`, and restarts
`influxdb3-explorer.service`. Neither the reference nor its resolved value is
baked into cloud-init. The Ubuntu/amd64-only Zabbix and NMS agent injections
remain off; QEMU guest-agent injection remains on, and the Explorer installer
stays the last command in the fully injected `runcmd` list.

This is also a runtime bake boundary. For a template marked
`provisions_service = "influxdb3-explorer"`, netbox-packer parses and validates
the fully injected YAML immediately before calling proxbox-api. A Core
endpoint/config file, credential-bearing key or value, private key, encoded
`write_files` content that cannot be inspected, or any non-placeholder
`nms-secret:` reference aborts the build with a log entry; proxbox-api is not
called. The pristine seed checks remain useful, but are not a substitute for
validating the editable payload that is actually baked.

## Base Image Pinning (reproducible, verifiable OS bases)

A cloud-init bake downloads a vendor base image that becomes the guest's **entire
operating system**. By default `netbox-packer` derives that URL from the template's
`os_family`/`os_version` and points at the vendor's **mutable `latest`** directory,
sending no content digest — so rebuilding the same profile can silently produce a
different root filesystem, and the artifact is accepted with no integrity check.

`PackerTemplate` therefore carries two optional fields:

| Field | Purpose |
|---|---|
| `base_image_url` | An exact (normally dated) vendor artifact, replacing the derived release default. |
| `base_image_sha256` | The reviewed digest, forwarded to proxbox-api as `sha256` and verified after download. |

### Currently pinned: one profile

Migration `0029` pins **`influxdb-core-3.11.0-debian-13`** (VMID 9052) and nothing else:

```
base_image_url    = https://cloud.debian.org/images/cloud/trixie/20260509-2473/debian-13-genericcloud-amd64-20260509-2473.qcow2
base_image_sha256 = 34f5481f320aef28408720a861582dcfe3a81781ee69f3910a64c29ad5395b89
```

Migration `0029` fails closed if that exact template row is missing and requires its
compare-and-set to update exactly one row, so a rename, deletion, or concurrent edit
cannot be recorded as a successful migration that pinned zero profiles.

Every other seeded profile is still unpinned and still resolves the vendor's mutable
`latest` directory with no digest. That is a **known, accepted gap**, not an oversight —
it closes per profile, by an operator who has verified a digest. Pinning is not free:
changing the pin on a template that is already `ready` marks it pending for rebake, so a
blanket pin across the catalog demands estate-wide rebakes.

### Obtaining a digest you can defend

**Debian publishes only `SHA512SUMS` for cloud images.** There is no `SHA256SUMS`, and a
SHA-256 cannot be derived from a SHA-512, so the digest must come from hashing the
artifact yourself:

```bash
SNAP=20260509-2473                      # a DATED directory, never latest/
FILE=debian-13-genericcloud-amd64-$SNAP.qcow2
BASE=https://cloud.debian.org/images/cloud/trixie/$SNAP

# 1. the vendor's published checksum for this exact file
curl -sSf "$BASE/SHA512SUMS" | grep "  $FILE$"

# 2. the artifact itself
curl -sSfL --proto '=https' --tlsv1.2 -o "$FILE" "$BASE/$FILE"

# 3. THE STEP THAT MATTERS: prove the bytes you hashed are the bytes Debian published
#    a checksum for. If this does not match, stop — do not pin.
sha512sum "$FILE"

# 4. only now derive the value for base_image_sha256
sha256sum "$FILE"
```

Never skip step 3, and never copy a checksum out of a listing into the field: that proves
only that the listing and the field agree, while looking exactly like provenance.

**Limit worth stating plainly:** these snapshot directories carry no `SHA512SUMS.sign`, so
the trust chain is TLS to `cloud.debian.org` plus the published checksum. That is
meaningfully stronger than an unpinned mutable URL and meaningfully weaker than an
offline-verifiable Debian signature. Do not call a pinned image signature-verified.

To refresh a pin, repeat the whole procedure against the new snapshot — do not edit the
constants alone.
| `base_image_url_at_build` | Machine-managed resolved URL used by the last successful cloud-image build. |
| `base_image_sha256_at_build` | Machine-managed resolved digest used by the last successful cloud-image build. |

Both may also be supplied per build via `variable_overrides['image_url']` and
`variable_overrides['image_sha256']`, which take precedence over the template fields.

Image URLs may not contain URL userinfo, a query string, or a fragment. The UI,
API/model, and build-job boundaries parse and reject those components so inline
tokens (including signed-download query credentials) cannot be persisted or sent
to proxbox-api. Build logs, proxbox output, and provenance snapshots additionally
use a URL with those components removed as defense in depth. A future
authenticated-download flow must use an opaque secret reference, never an inline
URL credential.

Migration `0028` adds the two `*_at_build` snapshots to both `PackerBuild` and
`PackerTemplate`. On success, the build's resolved source and the template's
last-successful source are committed atomically with the timestamp. The transaction
locks and reloads the template before comparing its current declared pin with the
resolved source, so a mismatched override or a pin edit during the build writes `stale`
instead of `ready`. Readiness requires both ready status and no computed staleness. Pin
changes are therefore visible immediately: changing or clearing a declared pin makes
the old artifact stale, and a per-build pin that differs from the template also produces
a stale template. Pin drift is checked even when `max_age_days` is unset; automatic
remediation sets the template to `building` and dispatches the queued build after any
configured branch merge. A queued row left by the former create-only path is recovered
rather than blocking remediation forever.
An unpinned build records its derived URL with an empty digest and does not become stale
merely because historical unpinned snapshots are empty.

**The rule that matters: a pinned URL must carry a digest.** If an explicit URL comes
from either source and no digest resolves, `jobs._resolve_cloud_image_source()` raises
and the build fails closed. A pin without verification is the worst of both worlds — it
looks like provenance while guaranteeing nothing about the bytes, and it does not even
survive the vendor replacing the artifact at that URL. A malformed digest is refused
rather than forwarded; an uppercase digest is normalised, since hex is case-insensitive.

An **unpinned** release build still runs without a digest. Requiring one everywhere
would break every existing template at once, so that gap is closed per profile, by
pinning it.

### What this does NOT do

**No profile is pinned by this change.** Pinning requires two judgements that must not
be guessed: which dated vendor image a profile should target, and a digest that has
actually been *verified*. An invented or unverified digest is worse than none.

To pin a profile:

1. Choose a dated vendor directory instead of `latest` (for example a dated Debian
   cloud-image build, or an Ubuntu release-dated image).
2. Obtain the digest from the vendor's own published checksum file for that exact
   artifact — not from a mirror, a search result, or this repository's history.
3. Verify it: download the image, compute `sha256sum`, and confirm it matches the
   vendor's published value.
4. Set `base_image_url` and `base_image_sha256` on the template, and record in the
   template description (or the pinning PR) **where the digest came from and how it was
   verified**.
5. Rebake the profile so the recorded artifact is the one in use.

Pinning trades automatic upstream fixes for reproducibility, so a pinned profile needs a
periodic refresh to pick up base-image security updates.

## PowerDNS Authoritative + Recursor Template

Migration `0013_seed_powerdns_auth_recursor_cloud_init.py` seeds a co-hosted
PowerDNS image for internal DNS service VMs.

| Field | Value |
| --- | --- |
| Template name | `powerdns-auth-recursor-ubuntu` |
| Installer config | `powerdns-auth-recursor-ubuntu` |
| OS | Ubuntu `24.04` |
| Template VMID | `9019` |
| Proxmox endpoint | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` |
| Storage | `local` |
| Authoritative listener | `127.0.0.1:5300` |
| Recursor listener | VM primary IPv4 address on port `53` |

The default bake target is the CLUSTER01-DC01 PVE cluster host
`10.0.30.71`. Operators may override the node or VMID at build dispatch when a
different target is needed.

The cloud-init payload installs `pdns-server`, `pdns-backend-sqlite3`,
`pdns-recursor`, `qemu-guest-agent`, `sqlite3`, and `iproute2`. The
authoritative service uses the bundled SQLite3 backend and initializes the
`gsqlite3` schema at `/var/lib/powerdns/pdns.sqlite3`.

Authoritative is loopback-only:

- DNS: `local-address=127.0.0.1`, `local-port=5300`
- API: `webserver=yes`, `webserver-address=127.0.0.1`, `webserver-port=8081`
- API key placeholder: `PDNS_AUTH_API_KEY`

Recursor is the resolver surface:

- DNS: VM primary IPv4 on port 53
- API: `webserver=yes`, `webserver-address=127.0.0.1`, `webserver-port=8082`
- Local-zone forwarding: `PDNS_LOCAL_FORWARD_ZONES`, default
  `nmulti.cloud=127.0.0.1:5300`
- Optional recursive forwarding: `PDNS_LOCAL_FORWARD_ZONES_RECURSE` appends a
  `forward-zones-recurse` entry when provided
- API key placeholder: `PDNS_RECURSOR_API_KEY`

The recursor `allow-from` list is restricted to `127.0.0.1/8`, `10.0.0.0/8`,
`172.16.0.0/12`, `192.168.0.0/16`, and `::1/128`. Do not set it to
`0.0.0.0/0`; this template must not become an open resolver.

## Passbolt CE Template

Migration `0015_seed_passbolt_cloud_init.py` seeds the Passbolt CE secret-manager
image that hosts `https://credential.nmulti.cloud`.

| Field | Value |
| --- | --- |
| Template name | `passbolt-ce-ubuntu-2404` |
| Installer config | `passbolt-ce-ubuntu-2404` |
| Cloud-config source | `netbox_packer/seeds/passbolt-ce-ubuntu-2404.cloud-config.yaml` |
| OS | Ubuntu `24.04` |
| Template VMID | `9060` |
| Proxmox endpoint | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` |
| Storage | `local` |
| App base URL | `https://credential.nmulti.cloud` |

The cloud-config installs the native `passbolt-ce-server` package via the
official checksum-verified repo setup (nginx + php-fpm + a local MariaDB), makes
`PASSBOLT_PLUGINS_JWT_AUTHENTICATION_ENABLED=true` effective via php-fpm pool
`env[]` entries, and configures nginx WITHOUT
SSL (`passbolt/nginx-configuration-three-choices select none`) because nginx-nms
terminates TLS upstream — the guest serves plain HTTP on `:80`. The QEMU guest
agent and Zabbix Agent 2 (pointed at `zabbix.nmulti.cloud`) are injected at bake
time, so they are intentionally absent from the seed. No secret is baked: the
local DB password is generated on first boot into `/etc/passbolt/.db_password`,
and the production server OpenPGP key, JWT keys, and database are supplied by the
data migration from the existing Passbolt instance. SMTP is intentionally left
unconfigured until an operator wires a relay.

## File Server All-in-One Template

Migration `0014_seed_fileserver_allinone_cloud_init.py` originally seeded the
combined file server image used by File Server auto-provisioning. Migration
`0017_update_fileserver_agent_package_index.py` updates the current record.

| Field | Value |
| --- | --- |
| Template name | `tpl-fileserver-allinone-ubuntu-2404` |
| Installer config | `fileserver-allinone-cloud-config` version `1.0.1` |
| Cloud-config source | `netbox_packer/seeds/tpl-fileserver-allinone.cloud-config.yaml` |
| OS | Ubuntu `24.04` |
| Template VMID | `9300` |
| Proxmox endpoint | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` |
| Storage | `local` |
| NMS backend URL | `https://backend.nms.nmulti.cloud` |
| NetBox URL | `https://netbox.nmulti.cloud` |

The default bake target is CLUSTER01-DC01. Operators may override the node or
VMID at build dispatch when a different target is needed, but the seeded row is
the production convention for this image.

The cloud-config installs Samba AD/DC packages (`samba`, `samba-dsdb-modules`,
`samba-vfs-modules`, `winbind`, `libnss-winbind`, `libpam-winbind`,
`krb5-user`, `acl`, `attr`, `chrony`), Nextcloud web/PHP prerequisites
(`nginx`, `php-fpm`, `php-ldap`, `php-smbclient`, `php-pgsql`, `php-gd`,
`php-curl`, `php-zip`, `php-xml`, `php-mbstring`, `php-intl`, `php-bcmath`,
`php-gmp`, `php-imagick`, `smbclient`, `cifs-utils`, `postgresql-client`),
`qemu-guest-agent`, `zabbix-agent2`, and `python3-venv`.
`nms-fileserver-agent` is installed into `/opt/nms-fileserver-agent/venv` from
`NMS_FILESERVER_AGENT_PIP_SPEC` (default `nms-fileserver-agent==0.1.0`), not
through apt. Store the package-index identity on the singleton
`PackerPluginSettings` row: the username is plaintext metadata in
`fileserver_package_read_user`, while the token is written only through
`set_fileserver_package_read_token()` and stored in
`fileserver_package_read_token_encrypted` with the same Fernet cipher derived
from Django's `SECRET_KEY` that is used by the proxbox-api key.

Use the Django/NetBox shell (`manage.py nbshell` or `manage.py shell`) to set or
rotate the credential; there is no plugin REST endpoint for this settings row,
and the encrypted field is intentionally not directly editable in Django admin.
The raw token therefore stays out of request/API parameters and is supplied only
to the model's encrypted setter in an operator shell:

```python
from netbox_packer.models import PackerPluginSettings

settings_row = PackerPluginSettings.get_solo()
settings_row.fileserver_package_read_user = "nms-pkg-reader"
settings_row.set_fileserver_package_read_token("<gitea-package-read-token>")
settings_row.save()
```

The credentials must belong to a dedicated non-human Gitea identity, and the
token must have package-Read permission only—never use a personal token or
`PACKAGE_WRITE_TOKEN`. Dispatch fails closed if either setting is empty,
URL-encodes both values, and redacts the raw or encoded token from persisted
build output. The rendered golden image stores the authenticated N-MultiCloud
PyPI index in root-only `/etc/nms-fileserver-agent/pip.conf`. Public `httpx` is
installed from PyPI before the agent is installed from the sole private index
with `--no-deps`.

Because every clone inherits that root-only credential, rotate the token on the
`PackerPluginSettings` row and rebake VMID `9300` whenever the read token is
replaced; retire prior images and clones according to the credential-rotation
policy.
Migration `0017_update_fileserver_agent_package_index.py` repoints installations
that already ran migration 0014 at this v1.0.1 config and marks the template
pending for a replacement bake.

This image is software-only. The bake does not create a Samba domain, does not
run a Nextcloud tenant install, and does not include any tenant secret. `nginx`
is disabled, `smbd`/`nmbd`/`winbind` are masked,
`nms-fileserver-agent-enroll.service` is installed but disabled/not run on the
golden template, and `nms-fileserver-agent-heartbeat.timer` is disabled until
clone-time user-data provides the one-time enrollment token and starts the agent
lifecycle.

## Base Ubuntu LTS Cloud-init Templates

Migration `0016_seed_ubuntu_lts_base_cloud_init.py` seeds three minimal base
images that serve as the starting templates for the customer VM catalog.

| Field | Value |
| --- | --- |
| Template names | `ubuntu-2204-cloudinit-base` (VMID `9040`), `ubuntu-2404-cloudinit-base` (VMID `9041`), `ubuntu-2604-cloudinit-base` (VMID `9042`) |
| Installer config | `ubuntu-lts-base-cloud-config` (shared by all three) |
| OS | Ubuntu `22.04`, `24.04`, `26.04` respectively |
| Proxmox endpoint | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` |
| Storage | `local` |

This is the only seed migration with a fully reversible reverse function —
rolling it back deletes the three seeded rows.

The cloud-config content is intentionally minimal. `qemu-guest-agent`,
`zabbix-agent2`, and `ssh_pwauth: true` are all added by the build-time
monitoring-agent injection in `jobs.py` rather than baked into the seed
content directly. No secret is baked into the image: per-VM username,
password (`cipassword`), and SSH keys are supplied by Proxmox cloud-init at
clone time.

## Akvorado 2.4.0 Template

Migrations `0023_packertemplate_nms_agent_and_service_marker.py` and
`0024_seed_akvorado_cloud_init.py` add the optional NMS host-agent injection
fields, durable service marker, and Akvorado golden template.

| Field | Value |
| --- | --- |
| Template name | `akvorado-2.4.0-ubuntu-2404` |
| Installer config | `akvorado-2.4.0-ubuntu-2404-cloud-config` version `2.4.0` |
| Cloud-config source | `netbox_packer/seeds/akvorado-2.4.0-ubuntu-2404.cloud-config.yaml` |
| OS | Ubuntu `24.04` |
| Template VMID | `9070` |
| Proxmox endpoint | `https://10.0.30.71:8006` |
| Proxmox node / SSH host | `10.0.30.71` |
| Storage | `local` |
| Service marker | `akvorado` |
| Lifecycle unit | `akvorado.service` |

On first boot, the cloud-config checksum-verifies the Docker APT signing key,
installs Docker Engine and the Compose plugin, validates and pulls
`/opt/akvorado/docker-compose.yml`, then enables `akvorado.service`. That single
oneshot unit owns the complete Compose lifecycle with `up -d --wait` on start
and `down` on stop. The Compose stack pins:

- Kafka `apache/kafka:4.2.0` in single-node KRaft mode;
- Valkey `valkey/valkey:9.0` (there is no Redis server or Redis container);
- ClickHouse `26.3` (`clickhouse/clickhouse-server:26.3`);
- Akvorado console, inlet, outlet, and orchestrator at
  `quay.io/akvorado/akvorado:2.4.0`.

The console uses Akvorado's cache driver named `redis` to speak the
Redis-compatible protocol to `valkey:6379`; the backing service is Valkey.
Flow receivers listen on UDP `2055`, `4739`, and `6343`, the BMP receiver on
TCP `10179`, and the console only on loopback at `127.0.0.1:8081`. Akvorado's
console trusts an identity header from a fronting authenticating proxy and does
not authenticate users itself, so the template does not publish it on a
wildcard host address. Reach it through an SSH tunnel to loopback port `8081`,
or provision a separate authenticating reverse proxy explicitly. The shipped
`akvorado.yaml` plus inlet/outlet/console includes is a working credential-free
default, so no configuration RPC is required before the stack starts.

The seed sets `install_nms_agent=True` only on this template. The build-time
injector compiles the static agent from one pinned public commit using a
SHA256-verified Go toolchain, writes a root-only config pointing at
`https://backend.nms.nmulti.cloud`, and enables its systemd unit. It reuses the
agent's secure-prefix self-registration flow and contains no enrollment token,
backend signing key, or other trust material. Zabbix management inside that
agent is disabled because the existing Zabbix Agent 2 injection remains the
owner of Zabbix configuration. Its local service allowlist contains exactly
`akvorado.service`.

Created VMs already store the source template primary key in the
`source_packer_template` custom field. Downstream integration code follows
that existing lineage to the read-only
`PackerTemplate.provisions_service="akvorado"` marker instead of guessing from
a hostname or introducing another VM tag.

## Build Verification

After either current InfluxDB build completes, the template row should be
`ready` with result VMID `9050` or `9051`; the selected target cluster should
contain a template with a `cicustom` user-data snippet. Clone verification must
confirm the exact package version, held package state, systemd state, and local
health/readiness endpoint before typed RPC onboarding begins.

For InfluxDB 3 Explorer, VMID `9053` should be a template on the selected
endpoint. On a fresh clone, wait for `cloud-init status --wait`, confirm
`systemctl is-active influxdb3-explorer.service`, and inspect the running image
reference for the reviewed digest. `curl http://127.0.0.1:8080/` should answer,
while `/etc/influxdb3-explorer/config.json` should not exist before provision-time
automation resolves the clone's `nms-secret:<opaque-id>` reference. After
provisioning, confirm that file is `root:1500` mode `0640` and the unit restarted.

For the PowerDNS co-hosted template, VMID `9019` should be marked as a template
on `10.0.30.71`. On first boot from a clone, `pdns` should listen on
`127.0.0.1:5300`, `pdns-recursor` should listen on the primary IPv4 address on
port 53, both PowerDNS API webservers should bind to localhost, and no
configuration should expose recursion to `0.0.0.0/0`.

For the File Server all-in-one template, VMID `9300` should be marked as a
template on `10.0.30.71`. On a clone before tenant provisioning, Samba and
nginx should remain inactive, `zabbix-agent2` should point at
`zabbix.nmulti.cloud`, and `/etc/nms-fileserver-agent/config.env` should contain
only the production `NMS_BACKEND_URL` and `NETBOX_URL` values.

For Akvorado, VMID `9070` should be marked as a template on `10.0.30.71` and
retain the cloud-init snippet. On a fresh clone, wait for
`cloud-init status --wait`, then verify `systemctl is-active akvorado.service`
and `systemctl is-active nms-agent.service`. `docker compose -f
/opt/akvorado/docker-compose.yml ps` should show all seven services running and
the Kafka, Valkey, ClickHouse, orchestrator, and console health checks should
settle healthy. The agent journal should show a bootstrap attempt against
`backend.nms.nmulti.cloud`; authorization still follows the existing
secure-prefix policy.

## Regression Coverage

`tests/test_cloud_config_build_static.py` locks the cloud-init build contract:

- the cloud-config branch delegates to `proxbox-api /cloud/templates/images`;
- the client performs plan → signed preflight → execute in order, keeps all
  build fields stable, and forwards the returned plan token;
- unavailable/not-ready/incompatible preflight, missing or expired tokens,
  execute-time plan rejection, and unverified execution all fail the build;
- unset target nodes are sent as `None`, not an empty string;
- historical migration `0007` remains byte-for-byte unchanged while additive
  migration `0020` safely retires its database row;
- OSS 2.9.1 and Core 3.11.0 cloud-configs parse as YAML, pin and hold exact
  package versions, verify the InfluxData signing key, and contain no setup
  request or credential material;
- Explorer 1.9.0 uses the exact `influxdata/influxdb3-ui` manifest digest,
  Debian `docker.io`, a loopback-default systemd lifecycle, bounded first boot,
  collision-safe VMID `9053` seeding, and no baked Core connection or credential;
- build requests validate explicit endpoint, node, VMID, and storage selectors
  and suppress legacy SSH-host metadata when an endpoint ID is selected;
- project docs and LLM files cover both profiles, typed RPC onboarding, and the
  `nms-secret:` boundary.
- the PowerDNS co-hosted seed keeps `pdns-server`, `pdns-recursor`,
  `qemu-guest-agent`, `127.0.0.1:5300`, private `allow-from` ranges, and
  reversible seeded-row cleanup stable.
- the File Server all-in-one seed keeps `tpl-fileserver-allinone-ubuntu-2404`,
  `fileserver-allinone-cloud-config`, VMID `9300`, CLUSTER01-DC01 endpoint
  `https://10.0.30.71:8006`, production NMS URLs, service-disabled defaults,
  root-only package-index configuration, package-Read credential placeholders,
  YAML parseability, and reversible seeded-row cleanup stable.
- the Akvorado seed keeps VMID `9070`, Kafka `4.2.0`, Valkey `9.0`, ClickHouse
  `26.3`, Akvorado `2.4.0`, the exact `akvorado.service` lifecycle contract,
  loopback-only console, working default config, HTTPS-only agent backend,
  agent opt-in/default-off behavior, source-template marker, and structural
  agent deduplication stable.
