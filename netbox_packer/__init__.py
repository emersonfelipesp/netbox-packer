from netbox.plugins import PluginConfig

from .compat import (
    PLUGIN_MAX_VERSION,
    PLUGIN_MIN_VERSION,
    register_netbox_compatibility_check,
)


class NetBoxPackerConfig(PluginConfig):
    name = "netbox_packer"
    verbose_name = "NetBox Packer"
    description = "Manage Packer VM template builds and catalog"
    version = "0.0.5"
    base_url = "packer"
    author = "Emerson Felipe"
    author_email = "emersonfelipe.2003@gmail.com"
    # Sourced from .compat so the backward-compatible stable GA contract is
    # declared in one place across the Proxbox plugin stack.
    min_version = PLUGIN_MIN_VERSION
    max_version = PLUGIN_MAX_VERSION
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
        register_netbox_compatibility_check(self)
        from . import (
            hcp_sync,  # noqa: F401 — registers PackerHCPSyncJobRunner
            jobs,  # noqa: F401 — registers PackerBuildJob and PackerStalenessCheckJob
            template_content,  # noqa: F401 — registers Derived VMs tab extension
        )


config = NetBoxPackerConfig
