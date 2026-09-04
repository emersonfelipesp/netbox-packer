# Version 0.0.5.post1

## NetBox 4.7.0 GA compatibility

- Added official NetBox 4.7.0 GA support while preserving the existing 4.5.8
  through 4.6.x compatibility range.
- Retained experimental warnings for pre-release identities and fail-closed
  numeric refusal for later 4.7 patch releases until separately certified.
- Corrected the plugin migration dependency so fresh NetBox 4.5.8 installs do
  not depend on a migration introduced after that release.
- Added immutable NetBox source and Docker image provenance to the GA evidence.
- Documented the reviewed `proxbox-api 0.0.20` and
  `netbox-proxbox 0.0.25.post2` capability releases for cloud-init builds.
