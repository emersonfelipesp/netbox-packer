from netbox.search import SearchIndex, register_search

from .models import PackerInstallerConfig, PackerTemplate


@register_search
class PackerTemplateIndex(SearchIndex):
    model = PackerTemplate
    fields = (
        ("name", 100),
        ("os_version", 300),
        ("proxmox_node", 500),
        ("packer_template_ref", 500),
        ("description", 2000),
    )
    display_attrs = ("name", "os_family", "os_version", "build_status")


@register_search
class PackerInstallerConfigIndex(SearchIndex):
    model = PackerInstallerConfig
    fields = (
        ("name", 100),
        ("version", 300),
        ("description", 2000),
    )
    display_attrs = ("name", "os_family", "installer_type", "version")
