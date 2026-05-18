from netbox.plugins import PluginConfig


class NetBoxPackerConfig(PluginConfig):
    name = "netbox_packer"
    verbose_name = "NetBox Packer"
    description = "Manage Packer VM template builds and catalog"
    version = "0.0.2"
    base_url = "packer"
    author = "Emerson Felipe"
    author_email = "emersonfelipe.2003@gmail.com"
    min_version = "4.5.0"
    max_version = "4.6.99"
    default_settings = {
        "PACKER_BUILD_TIMEOUT_SECONDS": 3600,
        "PACKER_STALENESS_CHECK_INTERVAL": "0 */6 * * *",
    }

    def ready(self):
        super().ready()


config = NetBoxPackerConfig
