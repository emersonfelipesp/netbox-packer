# NetBox Plugin Certification Evidence

This checklist tracks readiness for the NetBox Plugin Certification Program.

| Requirement | Evidence |
| --- | --- |
| Open source license | Apache-2.0 in `LICENSE` and `pyproject.toml` |
| Package metadata | PyPI project `netbox-packer`, project URLs, classifiers, Python `>=3.12` |
| NetBox compatibility | Plugin config declares `min_version = "4.5.8"` and `max_version = "4.6.99"` |
| Dependency policy | Standalone NetBox plugin; optional HCP Packer Registry integration uses HTTPS APIs |
| CI | GitHub Actions run lint, static tests, NetBox integration tests, docs, page coverage, screenshot capture, and release validation |
| Documentation | README, MkDocs site, installation, roadmap, release notes, and support links |
| Screenshots | `.github/workflows/docs-screenshots.yml` captures deterministic NetBox v4.6.4 UI screenshots into `docs/assets/screenshots` |
| Icon | NetBox menu uses Material Design Icons class `mdi mdi-package-variant-closed` |
| Maintainer access | Repositories stay under `emersonfelipesp`; NetBox Labs staff can be invited as collaborators when requested |

## Application Summary

- Repository: <https://github.com/emersonfelipesp/netbox-packer>
- Documentation: <https://emersonfelipesp.github.io/netbox-packer/>
- PyPI: <https://pypi.org/project/netbox-packer/>
- Support: <https://github.com/emersonfelipesp/netbox-packer/issues>
- Certification target release: `0.0.4`
- Verified NetBox targets: `v4.5.8`, `v4.5.9`, `v4.6.0`, `v4.6.1`, `v4.6.2`, `v4.6.3`, and `v4.6.4`
