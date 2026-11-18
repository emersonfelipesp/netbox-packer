# netbox-packer

NetBox plugin that reflects **HashiCorp Packer** image-build artifacts —
Proxmox VM templates, OPNsense / pfSense appliance images, and network OS
golden images — into NetBox through the
[`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api) backend.

`netbox-packer` is part of the Proxbox plugin family but is installable as a
standalone NetBox plugin. It can be deployed alongside
[`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) when a
Proxmox inventory workflow needs both VM synchronization and Packer template
cataloging.

When configured with an HCP Packer organization / project ID, the plugin can
resolve image IDs from the HCP Packer registry via `proxbox-api`.

## Status

`netbox-packer` v0.0.2.post2 ships Packer template, build, installer-config,
build-target, staleness, and HCP Packer registry sync support. This post
release normalizes certification evidence, packaging metadata, and
compatibility documentation without changing runtime behavior.

## Compatibility

See [COMPATIBILITY.md](COMPATIBILITY.md) for the full version compatibility table.

## Installation

```bash
pip install netbox-packer
```

In `configuration.py`:

```python
PLUGINS = [
    "netbox_packer",
]
```

```bash
python manage.py migrate
```

## Documentation

Full documentation is published at
<https://emersonfelipesp.github.io/netbox-packer/>.

## Support

Use GitHub Issues for bugs and feature requests:
<https://github.com/emersonfelipesp/netbox-packer/issues>.

## Certification Status

Certification evidence is tracked in [CERTIFICATION.md](./CERTIFICATION.md).
The repository includes Apache-2.0 licensing, PyPI metadata, compatibility
metadata, GitHub Actions CI, release validation, docs publishing, screenshot
capture, and page-coverage workflows for NetBox v4.6.1.

## CI and screenshot review

Public CI preserves the original lint, formatting, complete static-test selection, package metadata and NetBox v4.6.1 source checks. The source check uses Python 3.13, PostgreSQL 16 and Redis 7. Docker smoke keeps both original NetBox image rows. `CI complete` and `E2E complete` fail unless every required original producer succeeds. Main and maintenance branch admission remain unchanged. Only obsolete runs within one pull request can supersede each other; main runs remain independent.

Ruff is pinned to the compatible 0.15.8 version so an unreviewed tool update cannot change lint behavior. All existing rule selections and failure checks remain enabled. Workflow contracts also verify the original producers, matrix rows, service versions and failure behavior of the complete statuses.

The manual/tag screenshot workflow now validates captured screenshots and uploads them as a reviewable artifact. It retains screenshot generation and the strict documentation build. It no longer commits or pushes directly to main with skip-ci text, and it no longer publishes an unmerged screenshot candidate to gh-pages. This deliberately replaces that automatic writer with an artifact handoff.

Download the `netbox-packer-screenshots-<run-id>-<attempt>` artifact from the capture run. Review the files and submit the intended changes under `docs/assets/screenshots/` through a normal source pull request. The separate documentation workflow publishes only normally merged main source. Screenshot artifacts are validation output; uploading them does not publish the documentation or merge a source change.

For a separately authorized CI qualification, the temporary repository variable `CI_QUALIFICATION_ONLY=true` suppresses only the documentation publication step. The strict documentation build still runs. Remove that variable after the admitted main executions finish. The normal default remains publication from main pushes. This guard does not dispatch or qualify a release, package publisher or screenshot run.

## License

Apache-2.0
