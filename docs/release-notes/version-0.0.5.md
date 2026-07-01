# Version 0.0.5

## Packer template form: OS version dropdown + easier cloud-init template creation

The template add/edit form now guides operators through creating cloud-init
templates instead of relying on free-text entry.

- **OS version is a dropdown.** `os_version` is rendered as a select grouped by
  OS family (Ubuntu / Debian / RHEL / Proxmox), matching how OS family already
  behaves. With JavaScript enabled, the list narrows to the selected family; the
  grouped optgroups keep it usable with JavaScript disabled. Offered versions
  are defined once in `OS_VERSIONS_BY_FAMILY` (`choices.py`) and can be extended
  without a migration.
- **No data loss on edit.** A template whose stored `os_version` is outside the
  offered list keeps that value selectable, so editing an older template never
  fails validation.
- **The form stays free-form for automation.** The model field and REST API
  remain a plain string; only the web form constrains the value to a dropdown.
- **Decluttered form.** Machine-managed lifecycle fields (`built_at`,
  `packer_template_ref`, `installer_config_checksum_at_build`) are hidden from
  the form — they are written by `PackerBuildJob`, and remain available via the
  REST API.
- **Guidance help text** was added to `os_version`, `proxmox_template_id`,
  `storage_pool`, `cloud_init_ready`, and `installer_config`.

No database migration is required; this is a form/UX change only.
