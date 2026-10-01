# Unreleased

## Proxmox endpoint URL is now settable over REST

`proxmox_endpoint` was a model field but was missing from the REST serializers,
so a build target created through the API stored a blank endpoint and a Cloud-Init
template build failed at enqueue with "Proxmox endpoint URL is required". The
field is now readable and writable on both `packer-templates` and
`build-targets`.

- **Validated at the API boundary.** A value must be blank or an absolute `http`
  or `https` origin with a host (no path other than `/`). Paths, credentials,
  query strings, fragments,
  whitespace/control characters, and secret-shaped text are rejected, and the
  error never echoes the submitted value.
- **Still fail closed.** Storing a URL is not authorization. Dispatch continues
  to require exactly one matching netbox-proxbox `ProxmoxEndpoint` with the
  `enabled`, `allow_writes`, and `allow_packer_template_builds` gates, so an
  unknown or unauthorized URL is rejected before queueing.
- **Clearer enqueue error.** When no URL is available, the error now names the
  missing input: the template (when it has no build targets) or the build target
  for the selected node. It also states that `variable_overrides.endpoint_id`
  never supplies or authorizes an endpoint URL.

No database migration is required; the model field already existed.
