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
        # HCP Packer Registry (optional; all settings must be set to enable)
        "HCP_CLIENT_ID": "",
        "HCP_CLIENT_SECRET": "",
        "HCP_ORGANIZATION_ID": "",
        "HCP_PROJECT_ID": "",
        "HCP_SYNC_INTERVAL": "0 */4 * * *",  # cron: every 4 hours
        # Build dispatch
        "MAX_CONCURRENT_BUILDS_PER_NODE": 2,
    }

    def ready(self):
        super().ready()
        from . import (
            hcp_sync,  # noqa: F401 — registers PackerHCPSyncJobRunner
            jobs,  # noqa: F401 — registers PackerBuildJob and PackerStalenessCheckJob
            template_content,  # noqa: F401 — registers Derived VMs tab extension
        )


config = NetBoxPackerConfig
