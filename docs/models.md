# Data Models

`netbox-packer` defines five Django models. All extend `NetBoxModel` and support
NetBox's standard features (change logging, tags, custom fields, object
permissions).

---

## PackerInstallerConfig

An OS-installer configuration file stored verbatim in the database. Used as the
content source for a `PackerBuild`. The `checksum` field (SHA-256 hex digest) is
computed automatically on every `save()` and written to
`PackerTemplate.installer_config_checksum_at_build` at build time so staleness
detection can identify out-of-date templates.

| Field | Type | Notes |
| --- | --- | --- |
| `name` | CharField(100) | — |
| `os_family` | CharField(20) | Choices from `OSFamilyChoices` |
| `installer_type` | CharField(20) | `autoinstall`, `kickstart`, `preseed`, or `cloud_config` |
| `content` | TextField | Verbatim installer payload (e.g. the full `#cloud-config` YAML) |
| `version` | CharField(40) | Default `"1.0.0"` |
| `checksum` | CharField(64) | SHA-256 hex of `content`; auto-computed, not editable |
| `description` | TextField | Blank allowed |

**Unique constraint:** `(name, version)`.

**`installer_type` values:**

| Value | Label |
| --- | --- |
| `autoinstall` | Cloud-init autoinstall (Ubuntu) |
| `kickstart` | Anaconda kickstart (RHEL-family) |
| `preseed` | d-i preseed (Debian) |
| `cloud_config` | Cloud-config YAML (#cloud-config, Proxmox/generic) |

Only `cloud_config` triggers the proxbox-api cloud-init bake path. All other
types are run via `packer build`.

---

## PackerTemplate

A Proxmox VM template definition with lifecycle tracking. Ties together an
installer config, target node/storage, and monitoring-agent injection preferences.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | CharField(100) | — | — |
| `os_family` | CharField(20) | — | Choices from `OSFamilyChoices` |
| `os_version` | CharField(40) | — | e.g. `24.04`. The web form renders this as a dropdown grouped by OS family (`OS_VERSIONS_BY_FAMILY` in `choices.py`); the model/API stay free-form so automation can send any version. |
| `proxmox_template_id` | PositiveIntegerField | — | Proxmox VMID for the resulting template |
| `proxmox_endpoint` | URLField | blank | Proxmox API URL (used to derive SSH host) |
| `proxmox_node` | CharField(100) | — | Proxmox node name or IP |
| `storage_pool` | CharField(100) | blank | Proxmox storage pool name (default: `local`) |
| `storage_pool_type` | CharField(20) | blank | Choices from `StoragePoolTypeChoices` |
| `storage_format` | CharField(10) | blank | Choices from `StorageFormatChoices` |
| `cloud_init_ready` | BooleanField | `True` | Template supports cloud-init |
| `min_cpu_type` | CharField(40) | blank | Minimum CPU type required |
| `build_status` | CharField(20) | `pending` | `pending`, `building`, `ready`, `failed` |
| `built_at` | DateTimeField | null | Set by `PackerBuildJob` on success |
| `packer_template_ref` | CharField(255) | blank | Path/ref to the HCL2 Packer template file |
| `max_age_days` | PositiveIntegerField | null | Trigger staleness after N days |
| `auto_rebuild` | BooleanField | `False` | Auto-rebuild stale templates |
| `description` | TextField | blank | — |
| `installer_config` | FK → PackerInstallerConfig | null | SET_NULL; drives build type |
| `installer_config_checksum_at_build` | CharField(64) | blank | Snapshot of checksum at last successful build |

!!! note "Template form vs. model fields"
    The template add/edit form intentionally hides machine-managed lifecycle
    fields (`built_at`, `packer_template_ref`, and
    `installer_config_checksum_at_build`) — they are written by
    `PackerBuildJob`, not by operators. The full set of fields is still
    available through the REST API. The form also renders `os_version` as an
    OS-family-grouped dropdown and carries help text on the key
    cloud-init-template fields (`os_version`, `proxmox_template_id`,
    `storage_pool`, `cloud_init_ready`, `installer_config`).

### Monitoring agent injection fields (added in migration `0008`)

These fields control what `PackerBuildJob._inject_monitoring_agents()` adds to
`cloud_config` content at build time. They have no effect on non-`cloud_config`
installer types.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install_qemu_guest_agent` | BooleanField | `True` | Inject `qemu-guest-agent` package + `systemctl enable --now` runcmd into the cloud-config; skipped if `qemu-guest-agent` already appears in the installer config's packages list |
| `install_zabbix_agent2` | BooleanField | `True` | Inject Zabbix Agent 2 bootstrap script into `write_files` + `runcmd`; skipped entirely when `"zabbix-agent2"` appears anywhere in the installer config (e.g. the Zabbix server seed manages its own agent) |
| `zabbix_server` | CharField(255) | `"zabbix.nmulti.cloud"` | `ServerActive=` value in the injected `zabbix_agent2.conf`; validated against hostname/IP + optional `:port`, comma-separated; no spaces or shell metacharacters |

### HCP Packer fields

`hcp_bucket_name`, `hcp_channel_name`, `hcp_iteration_id`, `hcp_build_id`,
`hcp_last_synced_at` — store HCP Packer registry metadata. All blank by default.

### Computed properties

| Property | Returns |
| --- | --- |
| `age_days` | Days since `built_at`, or `None` when not yet built |
| `is_stale` | `True` when `age_days > max_age_days` or `installer_config.checksum != installer_config_checksum_at_build` |
| `derived_vms` | `VirtualMachine` queryset where `custom_field_data__source_packer_template == self.pk` |

---

## PackerBuild

A single build-run record for a `PackerTemplate`. Created by
`PackerTemplateViewSet.build()` and executed asynchronously by `PackerBuildJob`.

!!! warning "Not a jobs-assignable object type"
    `PackerBuild` is **not** registered as a NetBox jobs-assignable object type.
    Always enqueue with `PackerBuildJob.enqueue(build_id=build.pk)` and **never**
    pass `instance=build`. Passing `instance=` raises
    `"Jobs cannot be assigned to this object type"` and the Build button will
    silently do nothing.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `template` | FK → PackerTemplate | — | CASCADE |
| `triggered_by` | CharField(100) | blank | Username or trigger source |
| `queued_at` | DateTimeField | auto_now_add | — |
| `started_at` | DateTimeField | null | Set when job starts running |
| `finished_at` | DateTimeField | null | Set on success or failure |
| `status` | CharField(20) | `queued` | `queued`, `running`, `success`, `failed`, `cancelled` |
| `variable_overrides` | JSONField | `{}` | Per-build overrides (e.g. `image_url`, `ssh_host`) |
| `log` | TextField | blank | Accumulated build output |
| `exit_code` | IntegerField | null | Exit code from `packer build` or proxbox-api response |
| `result_template_id` | IntegerField | null | Proxmox VMID of the completed template |
| `selected_node` | CharField(100) | blank | Proxmox node selected by `select_build_node()` |

---

## PackerBuildTarget

A multi-cluster target entry for distributing builds across Proxmox nodes.
`select_build_node()` iterates enabled targets in ascending `priority` order,
skipping nodes at `MAX_CONCURRENT_BUILDS_PER_NODE` capacity. Falls back to the
template's primary node when no targets exist or all targets are exhausted.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `template` | FK → PackerTemplate | — | CASCADE; `related_name="build_targets"` |
| `proxmox_endpoint` | URLField | blank | Proxmox API URL for this target |
| `proxmox_node` | CharField(100) | — | Node name or IP |
| `priority` | PositiveIntegerField | `10` | Lower = higher priority |
| `enabled` | BooleanField | `True` | Disabled targets are skipped |

**Unique constraint:** `(template, proxmox_node)`.

---

## PackerPluginSettings

Singleton settings row for the plugin. Exactly one row exists; use
`PackerPluginSettings.get_solo()` to retrieve it.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `singleton_key` | CharField(32) | `"default"` | Not editable; forced to `"default"` on every `save()` |
| `branching_enabled` | BooleanField | `False` | When `True`, `PackerStalenessCheckJob` uses netbox-branching for stale updates |
| `branch_name_prefix` | CharField(64) | `"packer-stale"` | Prefix for auto-created branch names |
| `branch_on_conflict` | CharField(16) | `"fail"` | `"fail"` or `"acknowledge"` — behavior on branching merge conflicts |
| `proxbox_api_url` | URLField | blank | Base URL of the proxbox-api backend; required for `cloud_config` builds |
| `proxbox_api_key_encrypted` | CharField(512) | blank | Fernet-encrypted API key; not editable directly — use `set_proxbox_api_key()` |

### Key-management methods

```python
settings_row = PackerPluginSettings.get_solo()

# Store a new API key (encrypts in-place using settings.SECRET_KEY)
settings_row.set_proxbox_api_key("my-secret-key")
settings_row.save()

# Retrieve the decrypted key at job time
api_key = settings_row.get_proxbox_api_key()
```

The Fernet cipher is derived from `settings.SECRET_KEY` (SHA-256 → base64url).
There is **no dependency on `netbox-nms`** for key management.
