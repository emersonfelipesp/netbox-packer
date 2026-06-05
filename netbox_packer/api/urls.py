from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register("packer-templates", views.PackerTemplateViewSet)
router.register("build-jobs", views.PackerBuildViewSet)
router.register("installer-configs", views.PackerInstallerConfigViewSet)
router.register("build-targets", views.PackerBuildTargetViewSet)

urlpatterns = router.urls
