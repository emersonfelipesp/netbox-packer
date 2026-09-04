# NetBox Plugin Certification Evidence

This checklist tracks readiness for the NetBox Plugin Certification Program.

| Requirement | Evidence |
| --- | --- |
| Open source license | Apache-2.0 in `LICENSE` and `pyproject.toml` |
| Package metadata | PyPI project `netbox-packer`, project URLs, classifiers, Python `>=3.12` |
| NetBox compatibility | Plugin config declares the backward-compatible `4.5.8`–`4.7.99` range, including official `v4.7.0` GA |
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
- Verified NetBox targets: `v4.5.8`, `v4.5.9`, `v4.6.0`, `v4.6.1`, `v4.6.2`, `v4.6.3`, `v4.6.4`, and official `v4.7.0` GA at `5f06007e4c9bacc93ce17c1e645fc1143d60df3d`
- Docker GA evidence uses `netboxcommunity/netbox:v4.7.0-5.1.0@sha256:73a54ff279461170032b59a57a1930929965e3ba15c195af59f4b5f6d39a84a9`.
