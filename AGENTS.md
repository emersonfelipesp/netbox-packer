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

Migration `0026` brings both `0020` profiles (and the legacy `9011` row, which `0020`
rewrote with the OSS 2 content) to parity with the `0025` hardening. All three shared
its shell shape and therefore its three defects:

- **the keyring trust boundary admitted extra keys** — proving the downloaded file
  *contained* the fingerprint and then dearmoring all of it also trusts an attacker key
  bundled alongside the genuine one, which can sign repository metadata and gain root
  during `apt-get install`. Now: isolated `GNUPGHOME`, export only the expected primary
  fingerprint, assert the exported keyring holds exactly one `pub` record;
- **prereleases were accepted as the pinned release** — a `~` version sorts *before* the
  release it qualifies, so the script could install and hold an unreviewed build while
  reporting success. Now: final versions only, `~` refused explicitly;
- **downloads and the readiness loop were unbounded** — `--retry` does not help against a
  server that completes TLS and then goes silent, so first boot could hang forever. Now:
  connection, per-attempt, and overall retry deadlines plus a size cap, and a bounded
  readiness loop.

The hardened sources are tracked verbatim at
`netbox_packer/seeds/influxdb-oss-2.9.1-ubuntu-2404.cloud-config.yaml` and
`netbox_packer/seeds/influxdb-core-3.11.0-ubuntu-2404.cloud-config.yaml`; the `0026`
constants must stay byte-identical to them.

Four behaviours of `0026` are deliberate and must not be "simplified":

- **A row that no longer matches the exact `0020` baseline fails the migration**, with
  the offending rows named. It is not rewritten (that would discard an operator edit)
  and not silently skipped (that would let an operator deploy believing the
  root-compromise vector was removed everywhere while an untouched row keeps it).
- **Rebake invalidation follows `installer_config_id`, not the template name.** Names
  are editable and several templates can share one config, so a renamed or additional
  consumer would otherwise keep `ready` state and its pre-hardening artifact. Any linked
  template whose recorded build checksum differs from the hardened checksum is marked
  `pending` — including when the content was already hardened by hand but the *artifact*
  was baked from legacy content.
- **The migration refuses to run while a build is queued or running against a linked
  template.** That build read the old content and would finish by writing `ready` over
  the rebake marker, leaving a vulnerable artifact recorded as current.
- **The reverse is a no-op**, because rolling back must not restore a keyring that
  accepts attacker-supplied keys, nor discard rebake state.

**An installer failure must stay visible.** Cloud-init shellifies `runcmd` into a plain
`/bin/sh` script with no `set -e`, and build-time injection appends the Zabbix bootstrap
*after* each profile's own installer — so a non-zero exit from the installer is masked
by a later command's success and cloud-init still reports success. Both profiles
therefore run under `set -Eeuo pipefail` with an **`EXIT`** trap that writes a durable
marker to `/var/lib/nms/influxdb-install-failed`, and the `rm` that used to delete the
installer was removed so a failed guest keeps its evidence.

**It must stay an `EXIT` trap, never an `ERR` trap.** Bash does not run an `ERR` trap for
an explicit `exit 1`, and these scripts reject prereleases, unexpected installed
versions, and readiness timeouts with exactly that — an `ERR` trap silently misses the
three failure modes most worth recording. The `EXIT` handler also owns the temporary
keyring cleanup, because a script may install only one `EXIT` trap.

Alongside it, `TERM`/`INT`/`HUP` are trapped to `exit 143`/`130`/`129`. On an untrapped
fatal signal bash still runs the `EXIT` trap, but `$?` can still hold the previous
command's successful status, so the handler would clean up and record nothing — a
systemd cancellation, guest shutdown, or external timeout would look like success.
Converting each signal to a non-zero exit is what makes those visible.

Do not reintroduce the `rm`, and do not rely on the runcmd exit status.

Migration `0025` adds `influxdb-core-3.11.0-debian-13` (VMID `9052`), the
**Debian 13** Core 3 profile. Its verbatim cloud-config is
`netbox_packer/seeds/influxdb-core-3.11.0-debian-13.cloud-config.yaml` and the
migration constant must stay byte-identical to it. It inherits every rule above
— endpoint-agnostic, credential-free, fingerprint-verified key, pinned-and-held
package — and adds a baked production posture that must not be relaxed:

- the managed `/etc/influxdb3/influxdb3-core.conf` binds only to
  `127.0.0.1:8181` with token authentication left enabled, sets
  `disable-telemetry-upload = true`, and **omits `plugin-dir`** so the Python
  Processing Engine stays disabled;
- an `influxdb3-core.service` drop-in supplies `Restart=on-failure`;
- first boot **refuses any release other than Debian 13** (plus non-`amd64`/
  `arm64` architectures and a missing systemd), rather than half-configuring an
  unverified platform;
- `node-id` is derived from the **per-VM SMBIOS UUID** (falling back to the
  per-instance machine-id), and the script fails closed if neither is readable.
  Do **not** switch this back to the hostname: the Proxmox clone pipeline reuses
  the template's cicustom meta-data, so clones can share a hostname and would
  then share a node identity.
- **Every `curl` in the installer must be time-bounded**, not just the readiness
  probe. The script is the final `runcmd` entry, so a `curl` that never returns
  hangs cloud-init forever and the clone never reaches the readiness diagnostics;
  `--retry` does not help, because a server that completes TLS and then stops
  sending data produces no error to retry. The readiness probes carry
  `--connect-timeout`/`--max-time` plus an overall loop deadline, and the signing-key
  download additionally caps `--retry-max-time` and `--max-filesize` so a hostile
  endpoint cannot fill the temp directory before the key is filtered by fingerprint.
  `test_influxdb3_core_debian13_seed_contract` asserts this over every `curl`
  invocation, so a new unbounded one fails the suite.
- `install_zabbix_agent2` and `install_nms_agent` are **False** on this template
  because the shared injectors are Ubuntu- and amd64-only (`ubuntu${VERSION_ID}`
  Zabbix package name; amd64-only NMS bootstrap). Do not enable them for a Debian
  or arm64 template until those injectors are OS-family- and architecture-aware.
  This also keeps this seed's installer the **last** `runcmd` entry — cloud-init's
  `runcmd` wrapper has no `set -e`, so a non-final failure would be masked.
- `jobs._resolve_cloud_image_url()` resolves Debian images by `os_version` through
  `_DEBIAN_CODENAMES`; it used to return Bookworm for every Debian row. Keep that
  map in step with `choices.OS_VERSIONS_BY_FAMILY[debian]`, and remember a
  `cloud_config` bake never runs cloud-init, so a wrong base image is not caught
  at build time.
- **Only the expected repository signing key may be trusted.** The script imports
  the downloaded key into an isolated `GNUPGHOME` and exports **only** the expected
  primary fingerprint (`--export-options export-minimal`), then asserts the exported
  keyring holds exactly one `pub` key with that fingerprint. Do not go back to
  `grep`-ing the downloaded file for the fingerprint and `gpg --dearmor`-ing the
  whole thing: a substituted file carrying the genuine key **plus** an attacker key
  passes that check, and the attacker key could then sign repository metadata and
  obtain root during `apt-get install`.
- **Only a final `3.11.0` package version is accepted.** A tilde sorts *before* the
  release it qualifies, so `3.11.0~rc1` is a prerelease; the candidate regex and the
  post-install `dpkg-query` check both refuse any `~` version, so the profile cannot
  silently install and hold an unreviewed vendor build while reporting success.
- **The collision guard must not compare mutable build state.** It excludes
  `build_status` and `packer_template_ref` (`_MUTABLE_BUILD_STATE_FIELDS`), because a
  successful bake flips `build_status` from `pending` to `ready` — comparing it would
  make rolling `0025` back and reapplying it raise a bogus collision on the row the
  migration created itself, blocking deployment recovery and migration replay.
- **Known accepted risk: the base image is the mutable vendor `latest`, with no
  digest.** This is pre-existing for every seeded profile, not specific to this one,
  and is tracked in issue #96 (pin a dated image plus a reviewed `sha256`, forwarded
  through `call_proxbox_build` to `proxbox-api`, failing closed when a pinned profile
  has none). Do not add a digest without recording how it was obtained and verified —
  an unverified digest looks like provenance while proving nothing.

Do not add a remote bind to this image: with token auth enabled, a non-loopback
listener would carry bearer tokens over plaintext HTTP. Put a TLS reverse proxy
in front of it, or use the audited RPC installer with explicit TLS material.

Do not add token creation to this seed. The first administrative token is
created and vaulted only by `service.influxdb.1.bootstrap` (`family="core3"`).
For hosts that already exist, `netbox-rpc` seeds
`os.linux.debian.13.preflight_influxdb3_core` (read) and
`os.linux.debian.13.install_influxdb3_core` (write, approval required), which
apply the same posture over audited SSH; the onboarding sequence is
`preflight` -> `install` -> `service.influxdb.1.bootstrap`. **`netbox-packer`
must not import, depend on, or reference `netbox-rpc`** — that dependency is
one-way, and these procedures are named here as documentation only.

## Signed Preflight Build Guardrail

Every executable cloud-config build uses the proxbox-api signed handshake in
this exact order: `POST /cloud/templates/images` with `execute=false` to obtain
the server-rendered `recipe_digest`; `POST /cloud/templates/images/preflight`
contract `1.0` with the endpoint, target, provider, storage fields, and digest to
obtain an unexpired `plan_token`; then `POST /cloud/templates/images` with the
same build fields, `execute=true`, and that token. Never compute the recipe
digest locally or allow request fields to drift between plan and execute.

This requires the signed-preflight contract in `proxbox-api >= 0.0.19.post5`.
Preflight unavailability or findings, `ready=false`, writes disabled, missing or
expired tokens, execute-time mismatch/expiry, and responses without both
confirmed execution and artifact verification fail the build with an actionable
log entry. A 404 from the preflight endpoint means the proxbox-api service is
incompatible; do not fall back to legacy one-step execution.

## Base Image Pin Guardrail

`PackerTemplate.base_image_url` / `base_image_sha256` (migration `0027`) pin a template
to an exact vendor artifact and its reviewed digest, which
`jobs._resolve_cloud_image_source()` forwards to proxbox-api as `sha256`.
`variable_overrides['image_url']` / `['image_sha256']` override them per build.

- **A pinned URL without a digest fails the build, by design.** Never "fix" that by
  making the digest optional for pinned URLs — an unverified pin looks like provenance
  while proving nothing.
- **Never invent, guess, or copy an unverified digest**, and never take one from this
  repository's history. Obtain it from the vendor's published checksum file for the
  exact artifact, verify it by downloading and hashing, and record where it came from.
- An **unpinned** release build still resolves the vendor's mutable `latest` directory
  with no digest. That is a known, accepted gap — closing it is per-profile pinning, not
  a blanket requirement, which would break every existing template at once.
- A malformed digest is refused rather than forwarded; an uppercase digest is
  normalised. Keep both behaviours.

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
on a config-deploy RPC. No container may use `latest`. Akvorado cannot
authenticate console users itself, so the console must remain bound to
`127.0.0.1:8081`; operators may reach it through an SSH tunnel or provision a
separate authenticating reverse proxy.

Migration `0023_packertemplate_nms_agent_and_service_marker.py` makes the NMS
host-agent injection optional and default-off. Only the Akvorado seed opts in,
points at `https://backend.nms.nmulti.cloud`, and sets the non-editable
`provisions_service="akvorado"` marker. Downstream code follows the existing
`source_packer_template` VM lineage to that marker. Agent enrollment must reuse
the existing secure-prefix bootstrap flow; never bake a token, signing key, or
new trust mechanism. Its local service allowlist contains exactly
`akvorado.service`, while the existing Zabbix injection remains responsible for
Zabbix Agent 2.
