from django.urls import include, path
from utilities.urls import get_model_urls

from . import views  # noqa: F401 — registers views via @register_model_view

urlpatterns = [
    # PackerTemplate
    path(
        "templates/",
        include(get_model_urls("netbox_packer", "packertemplate", detail=False)),
    ),
    path(
        "templates/<int:pk>/",
        include(get_model_urls("netbox_packer", "packertemplate")),
    ),
    # PackerBuild
    path(
        "builds/",
        include(get_model_urls("netbox_packer", "packerbuild", detail=False)),
    ),
    path(
        "builds/<int:pk>/",
        include(get_model_urls("netbox_packer", "packerbuild")),
    ),
    # PackerInstallerConfig
    path(
        "installer-configs/",
        include(get_model_urls("netbox_packer", "packerinstallerconfig", detail=False)),
    ),
    path(
        "installer-configs/<int:pk>/",
        include(get_model_urls("netbox_packer", "packerinstallerconfig")),
    ),
    # PackerBuildTarget
    path(
        "build-targets/",
        include(get_model_urls("netbox_packer", "packerbuildtarget", detail=False)),
    ),
    path(
        "build-targets/<int:pk>/",
        include(get_model_urls("netbox_packer", "packerbuildtarget")),
    ),
]
