"""netbox-packer — NetBox plugin for HashiCorp Packer image inventory.

Sibling plugin of `netbox-proxbox`. Reflects Packer-built Proxmox VM templates,
appliance images, and golden images into NetBox through `proxbox-api`.
"""

from __future__ import annotations

from netbox.plugins import PluginConfig

__version__ = "0.0.1"


class NetBoxPackerConfig(PluginConfig):
    name = "netbox_packer"
    verbose_name = "NetBox Packer"
    description = (
        "Reflect HashiCorp Packer image-build artifacts — Proxmox VM templates, "
        "appliance images, and golden images — into NetBox via proxbox-api."
    )
    version = __version__
    author = "Emerson Felipe"
    author_email = "emersonfelipe.2003@gmail.com"
    base_url = "packer"
    min_version = "4.5.8"
    max_version = "4.6.99"
    required_settings: list[str] = []
    default_settings = {
        # Fallback only — when netbox-proxbox is installed, its singleton
        # FastAPIEndpoint row is reused instead.
        "proxbox_api_url": "",
        "proxbox_api_key": "",
        # Optional HCP Packer registry pointer (channel-aware lookups).
        "hcp_packer_organization_id": "",
        "hcp_packer_project_id": "",
    }


config = NetBoxPackerConfig
