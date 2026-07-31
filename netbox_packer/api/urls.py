from django.urls import path
from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register("packer-templates", views.PackerTemplateViewSet)
router.register("build-jobs", views.PackerBuildViewSet)
router.register("installer-configs", views.PackerInstallerConfigViewSet)
router.register("build-targets", views.PackerBuildTargetViewSet)

urlpatterns = [
    path("influxdb-profiles/", views.InfluxDBProfileListView.as_view(), name="influxdb-profiles"),
    *router.urls,
]
