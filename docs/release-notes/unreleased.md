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

## Cloud-init builds reuse the netbox-proxbox proxbox-api linkage

netbox-packer kept its own `PackerPluginSettings.proxbox_api_url` and API key and
authorized every cloud-init build against them. The plugin has no settings page,
so a stored value that no longer matched the enabled netbox-proxbox FastAPI
endpoint made every build fail with "PackerPluginSettings.proxbox_api_url does not
match an enabled netbox-proxbox FastAPI endpoint".

netbox-proxbox is now the source of truth, and the packer settings are an optional
override:

- **Default linkage.** A blank `proxbox_api_url` uses the base URL (hostname URL
  preferred), API key, and TLS verification choice of the single enabled
  netbox-proxbox FastAPI endpoint. Zero or several enabled endpoints fail closed
  with a message that names netbox-proxbox as the place to configure it.
- **Explicit match.** A `proxbox_api_url` that matches exactly one enabled
  netbox-proxbox FastAPI endpoint selects it, which also disambiguates several
  enabled endpoints.
- **Stale values no longer brick builds.** Because the plugin has no page to edit or
  clear the setting, a non-blank value that matches no enabled endpoint is treated
  as stale: with exactly one enabled endpoint the stored URL and its stored API key
  are ignored together in favour of that endpoint and its own key (the stale
  backend's key is never sent to the selected backend, and an endpoint without a key
  fails closed), and a secret-free warning is written to the build log; with zero or
  several enabled endpoints the build fails closed. A stored URL is never used as a
  transport target unless it matches an enabled netbox-proxbox endpoint.
- **Authentication.** The stored packer API key is kept only when the stored URL is
  blank (the operator keyed the default linkage) or when a non-blank stored URL
  matches the selected endpoint; otherwise the key from the netbox-proxbox endpoint
  is used. A stale stored URL, including the value earlier documentation showed as
  an example, never carries its key to the selected endpoint. Keys are never logged
  or echoed.
- **Unchanged gates.** The selected Proxmox endpoint must still pass the
  netbox-proxbox gates and be translated to the backend endpoint id before dispatch
  and again at the final call boundary. The build log now records the derived
  proxbox-api base URL and which linkage was used.
- **Enabled-row cardinality.** An enabled netbox-proxbox endpoint whose request
  context cannot be built still counts as an enabled endpoint, so it cannot make a
  different backend look like the only one. A single unusable row fails closed with
  a message about its target fingerprint and key configuration.
- **No redirects with the API key.** Requests to proxbox-api now refuse every HTTP
  redirect (including https to http downgrades) with a secret-free error, so the key
  is never forwarded to another origin.
- The create-instance modal resolves the linkage the same way.

No database migration is required.
