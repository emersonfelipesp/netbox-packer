# Compatibility Matrix

> `proxbox-api` is a separately deployed backend service, not a Python package dependency.
> `netbox-packer` communicates with it over REST.

## NetBox support tiers

`netbox-packer` declares two NetBox support tiers, defined once in
[`netbox_packer/compat.py`](netbox_packer/compat.py) and vendored byte-identically across the
whole Proxbox plugin stack (`netbox-proxbox`, `netbox-ceph`, `netbox-packer`,
`netbox-pbs`, `netbox-pdm`):

| Tier | NetBox range | Constant | Behaviour |
|---|---|---|---|
| **Stable** | `4.5.8` – `4.6.99` | `STABLE_MIN_NETBOX_VERSION` / `STABLE_MAX_NETBOX_VERSION` | Admitted silently. Directly exercised in CI at v4.5.8 and v4.6.6; the rest of the band is admitted on the strength of those. |
| **Held beta** | canonical `4.7.0-beta2` metadata only | numeric 4.7 constants plus the release-identity guard | Loads and runs normally; warns once via `netbox_packer.W001`. Final 4.7.0 and every other 4.7 identity are rejected. |

`PluginConfig.min_version` is the stable floor and `PluginConfig.max_version`
is the held numeric ceiling (`4.7.0`). The shared v3 guard reads canonical
release metadata and admits only designation `beta2`; local metadata may add a
build label but cannot replace version or designation.

On a 4.7 install you will see one warning per plugin, from `manage.py check` and
in the startup log:

```
WARNINGS:
?: (netbox_packer.W001) NetBox Packer is running on NetBox 4.7.0-beta2, which is
   supported on an experimental basis only. Certified support covers NetBox
   4.5.8 through 4.6.99.
```

It is a warning, never an error — it cannot block NetBox from starting.

**To silence it**, set the key in this plugin's `PLUGINS_CONFIG` entry:

```python
PLUGINS_CONFIG = {
    "netbox_packer": {"silence_netbox_compatibility_warning": True},
}
```

That silences both the system check and the startup log line.

> Django's own `SILENCED_SYSTEM_CHECKS` is honoured too, but **not from
> `configuration.py`** — NetBox's `settings.py` imports an explicit list of
> named settings and that one is not on it, so setting it there has no effect.
> It only applies through NetBox's `local_settings.py` hatch, which upstream
> labels unsupported. Use the `PLUGINS_CONFIG` key above.

NetBox below `4.5.8`, final 4.7.0, other 4.7 identities, and 4.8+ are refused by
the stock numeric gate plus the held-line identity guard.

> **These tiers describe the *next* release, not the currently published
> package.** Every artifact published before this change declares
> `max_version = "4.6.99"` and will refuse NetBox 4.7 regardless of what this
> table says. `pip install` of an older version therefore still caps at 4.6.99.

### Upgrading to NetBox 4.7 means upgrading the whole plugin stack

A Proxbox-family plugin left at the old `4.6.99` ceiling does **not** stop
NetBox from starting. `netbox/settings.py` catches `IncompatiblePluginError`,
emits a Python `warnings.warn`, and **skips that plugin** — NetBox comes up
without it.

That is easy to miss and worth stating plainly, because the quiet failure is
the dangerous one. `warnings.warn` does not reach the application log in a
normal production deployment, so the visible symptom is not an error but an
*absence*: the plugin's navigation entries, views, REST API routes, and
background jobs are simply gone, and anything that depended on them fails later
and further away. A health probe against NetBox itself still returns 200.

So before moving an instance to beta2, upgrade **every** installed
Proxbox-family plugin to a release carrying compatibility contract v3, and
afterwards verify each one is actually registered rather than trusting that
NetBox started:

```bash
python manage.py shell -c "from django.apps import apps; print([p for p in ('netbox_proxbox','netbox_pbs','netbox_pdm','netbox_ceph','netbox_packer') if apps.is_installed(p)])"
```

On 4.5.8–4.6.x, mixed versions remain fine as before.

### netbox-branching does not support NetBox 4.7 yet

`netboxlabs-netbox-branching` declares `max_version = "4.6.99"` (checked
through 1.0.3), so on NetBox 4.7 **NetBox skips it** — the package stays
importable, but its Django app is absent from `INSTALLED_APPS` and its models
and schemas do not exist.

If you use branch-isolated sync (`branching_enabled = True`), **do not move to
NetBox 4.7 until a 4.7-capable netbox-branching release exists.** The
availability detector here now requires the loaded app rather than an
importable package, so a skipped branching app is correctly reported as
unavailable; but a sync configured for branch isolation that finds branching
unavailable currently proceeds against `main` rather than refusing, which
silently drops the isolation boundary you configured. Tightening that to
fail closed is tracked separately.

Installations that do not use branching are unaffected.

**Beta version strings.** NetBox's canonical `release.yaml` at beta2 reads
`version: "4.7.0"` with `designation: "beta2"`, while the stock plugin gate
receives only bare `RELEASE.version`. The numeric ceiling is therefore `4.7.0`;
the separate metadata guard distinguishes beta2 from GA and other prereleases.

**Current pre-release evidence.** The required source-checkout matrix runs
against exact NetBox `v4.7.0-beta2` commit
`aa1d49d0f5021a28e6efc2d0364b84c5bcec7137`; exact v4.5.8 and v4.6.6 cells
remain alongside it as backward-compatibility evidence.

| netbox-packer | NetBox | Python | netbox-proxbox | proxbox-api | pydantic |
|---|---|---|---|---|---|
| v0.0.5 | v4.5.8-v4.6.6 plus held v4.7.0-beta2 | ≥3.12 | capability-bearing revision after 0.0.25 for `cloud_config`; optional for local Packer | capability-bearing revision after 0.0.20 for `cloud_config` | ≥2.0.0 |
| v0.0.2.post2 | 4.5.8 – 4.6.x | ≥3.12 | Optional | Required | ≥2.0.0 |
| v0.0.2 | 4.5.x – 4.6.x | ≥3.12 | ≥0.0.16 | Required | ≥2.0.0 |

`proxbox-api 0.0.20` and `netbox-proxbox 0.0.25` are pre-capability releases;
use reviewed revisions after those tags until release engineering records the
exact validated inclusive version floors. This deliberately avoids a numeric
`>` placeholder, whose PEP 440 semantics could exclude a valid `.postN` release.
