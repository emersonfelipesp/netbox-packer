"""Template content extensions — adds Derived VMs tab to PackerTemplate detail."""
from netbox.plugins.utils import get_plugin_config
from netbox.views.generic.feature_views import ObjectChildrenView


class PackerTemplateDerivedVMsTab:
    """
    Registers a 'Derived VMs' tab on PackerTemplate detail pages.

    The tab is injected via netbox.plugins TemplateExtension mechanism.
    """
    pass


# Register the tab extension using netbox.plugins.TemplateExtension if available
try:
    from netbox.plugins import TemplateExtension

    class PackerTemplateDerivedVMsExtension(TemplateExtension):
        model = "netbox_packer.packertemplate"

        def full_width_page(self):
            template = self.context["object"]
            try:
                derived_vms = list(template.derived_vms.select_related("site", "cluster")[:50])
            except Exception:
                derived_vms = []
            return self.render(
                "netbox_packer/inc/derived_vms_tab.html",
                extra_context={"derived_vms": derived_vms},
            )

    template_extensions = [PackerTemplateDerivedVMsExtension]

except ImportError:
    template_extensions = []
