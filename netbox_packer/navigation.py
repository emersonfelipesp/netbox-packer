from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

menu = PluginMenu(
    label="Packer",
    groups=(
        (
            "Templates",
            (
                PluginMenuItem(
                    link="plugins:netbox_packer:packertemplate_list",
                    link_text="Packer Templates",
                    permissions=["netbox_packer.view_packertemplate"],
                    buttons=[
                        PluginMenuButton(
                            link="plugins:netbox_packer:packertemplate_add",
                            title="Add",
                            icon_class="mdi mdi-plus-thick",
                            permissions=["netbox_packer.add_packertemplate"],
                        ),
                    ],
                ),
                PluginMenuItem(
                    link="plugins:netbox_packer:packerinstallerconfig_list",
                    link_text="Installer Configs",
                    permissions=["netbox_packer.view_packerinstallerconfig"],
                    buttons=[
                        PluginMenuButton(
                            link="plugins:netbox_packer:packerinstallerconfig_add",
                            title="Add",
                            icon_class="mdi mdi-plus-thick",
                            permissions=["netbox_packer.add_packerinstallerconfig"],
                        ),
                    ],
                ),
            ),
        ),
        (
            "Builds",
            (
                PluginMenuItem(
                    link="plugins:netbox_packer:packerbuild_list",
                    link_text="Builds",
                    permissions=["netbox_packer.view_packerbuild"],
                    buttons=[
                        PluginMenuButton(
                            link="plugins:netbox_packer:packerbuild_add",
                            title="Add",
                            icon_class="mdi mdi-plus-thick",
                            permissions=["netbox_packer.add_packerbuild"],
                        ),
                    ],
                ),
                PluginMenuItem(
                    link="plugins:netbox_packer:packerbuildtarget_list",
                    link_text="Build Targets",
                    permissions=["netbox_packer.view_packerbuildtarget"],
                    buttons=[
                        PluginMenuButton(
                            link="plugins:netbox_packer:packerbuildtarget_add",
                            title="Add",
                            icon_class="mdi mdi-plus-thick",
                            permissions=["netbox_packer.add_packerbuildtarget"],
                        ),
                    ],
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-package-variant-closed",
)
