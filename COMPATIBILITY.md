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
| **Stable** | `4.5.8` – `4.6.99` | `STABLE_MIN_NETBOX_VERSION` / `STABLE_MAX_NETBOX_VERSION` | Certified and CI-gated. Silent. |
| **Experimental** | `4.7.0` – `4.7.99` | `EXPERIMENTAL_MIN_NETBOX_VERSION` / `EXPERIMENTAL_MAX_NETBOX_VERSION` | Loads and runs normally; warns once via system check `netbox_packer.W001`. |

`PluginConfig.min_version` is the stable floor; `PluginConfig.max_version` is the
**experimental** ceiling (`4.7.99`). Admitting 4.7 without an opt-in is
deliberate — an operator upgrading NetBox never has to touch plugin
configuration. Experimental support needs no setting, no flag, and no extra
install step.

On a 4.7 install you will see one warning per plugin, from `manage.py check` and
in the startup log:

```
WARNINGS:
?: (netbox_packer.W001) NetBox Packer is running on NetBox 4.7.0-beta1, which is
   supported on an experimental basis only. Certified support covers NetBox
   4.5.8 through 4.6.99.
```

It is a warning, never an error, so it cannot block NetBox from starting. Silence
it with Django's stock mechanism in `configuration.py` once the risk is accepted:

```python
SILENCED_SYSTEM_CHECKS = ["netbox_packer.W001"]
```

NetBox below `4.5.8` and from `4.8` onward is still refused outright by NetBox's
own plugin version gate.

### Upgrading to NetBox 4.7 upgrades the whole plugin stack at once

`PluginConfig.validate()` raises `IncompatiblePluginError` **while
`netbox/settings.py` is still executing**, so a single installed Proxbox-family
plugin whose `max_version` still reads `4.6.99` prevents NetBox from starting at
all — a failed boot, not a disabled plugin.

That makes the Proxbox-family plugins an all-or-nothing set on 4.7. Before moving
a NetBox instance to 4.7, upgrade **every** installed Proxbox-family plugin to a
release carrying the `4.7.99` ceiling. On 4.5.8–4.6.x, mixed versions remain fine
as before.

**Beta version strings.** NetBox's `release.yaml` at tag `v4.7.0-beta1` reads
`version: "4.7.0"` with `designation: "beta1"`, and `netbox/settings.py` passes
`RELEASE.version` — the bare `"4.7.0"` — to `PluginConfig.validate()`. The
`4.7.99` ceiling is sized for that comparison string; `RELEASE.full_version`
(`"4.7.0-beta1"`) is used only for display.

| netbox-packer | NetBox | Python | netbox-proxbox | proxbox-api | pydantic |
|---|---|---|---|---|---|
| v0.0.5 | v4.5.8, v4.5.9, v4.6.0-v4.6.4 | ≥3.12 | Optional | Required | ≥2.0.0 |
| v0.0.2.post2 | 4.5.8 – 4.6.x | ≥3.12 | Optional | Required | ≥2.0.0 |
| v0.0.2 | 4.5.x – 4.6.x | ≥3.12 | ≥0.0.16 | Required | ≥2.0.0 |
