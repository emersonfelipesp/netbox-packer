# NetBox Plugin Certification Evidence

This checklist tracks readiness for the NetBox Plugin Certification Program.

| Requirement | Evidence |
| --- | --- |
| Open source license | Apache-2.0 in `LICENSE` and `pyproject.toml` |
| Package metadata | PyPI project `netbox-packer`, project URLs, classifiers, Python `>=3.12` |
| NetBox compatibility | Stable `4.5.8`–`4.6.99` plus canonical `v4.7.0-beta2` metadata under the fail-closed v3 identity guard; final/other 4.7 identities remain held |
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
- Certification target release: `0.0.5`
- Verified historical targets extend through `v4.6.4`; the current matrix adds
  exact v4.5.8, v4.6.6, and beta2 revision
  `aa1d49d0f5021a28e6efc2d0364b84c5bcec7137`.
