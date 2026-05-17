"""NetBox navigation entries for netbox-packer."""

from __future__ import annotations

from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label="Packer",
    icon_class="mdi mdi-package-variant-closed",
    groups=(
        (
            "HashiCorp Packer",
            (
                PluginMenuItem(
                    link="plugins:netbox_packer:home",
                    link_text="Overview",
                ),
            ),
        ),
    ),
)
