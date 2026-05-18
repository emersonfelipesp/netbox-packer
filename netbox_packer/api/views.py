from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response

from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets, models
from .serializers import (
    PackerBuildSerializer,
    PackerBuildTargetSerializer,
    PackerInstallerConfigSerializer,
    PackerTemplateSerializer,
)


class PackerInstallerConfigViewSet(NetBoxModelViewSet):
    queryset = models.PackerInstallerConfig.objects.prefetch_related("tags")
    serializer_class = PackerInstallerConfigSerializer
    filterset_class = filtersets.PackerInstallerConfigFilterSet


class PackerTemplateViewSet(NetBoxModelViewSet):
    queryset = models.PackerTemplate.objects.select_related(
        "proxmox_endpoint",
        "installer_config",
    ).prefetch_related("tags")
    serializer_class = PackerTemplateSerializer
    filterset_class = filtersets.PackerTemplateFilterSet

    @action(detail=True, methods=["post"])
    def build(self, request, pk=None):
        """Queue a new build for this template."""
        template = self.get_object()
        build = models.PackerBuild.objects.create(
            template=template,
            triggered_by=str(request.user),
            variable_overrides=request.data.get("variable_overrides", {}),
            status="queued",
        )
        models.PackerTemplate.objects.filter(pk=template.pk).update(
            build_status="building"
        )
        serializer = PackerBuildSerializer(build, context={"request": request})
        return Response(serializer.data, status=http_status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="validate-node")
    def validate_node(self, request, pk=None):
        """Basic validation — returns warnings if endpoint or node is missing."""
        template = self.get_object()
        errors = []
        if not template.proxmox_endpoint_id:
            errors.append("No Proxmox endpoint configured")
        if not template.proxmox_node:
            errors.append("No Proxmox node configured")
        return Response({"valid": len(errors) == 0, "errors": errors})


class PackerBuildViewSet(NetBoxModelViewSet):
    queryset = models.PackerBuild.objects.select_related("template").prefetch_related(
        "tags"
    )
    serializer_class = PackerBuildSerializer
    filterset_class = filtersets.PackerBuildFilterSet


class PackerBuildTargetViewSet(NetBoxModelViewSet):
    queryset = models.PackerBuildTarget.objects.select_related(
        "template",
        "proxmox_endpoint",
    ).prefetch_related("tags")
    serializer_class = PackerBuildTargetSerializer
    filterset_class = filtersets.PackerBuildTargetFilterSet
