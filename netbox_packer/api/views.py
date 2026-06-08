from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response

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
        "installer_config",
    ).prefetch_related("tags")
    serializer_class = PackerTemplateSerializer
    filterset_class = filtersets.PackerTemplateFilterSet

    @action(detail=True, methods=["post"])
    def build(self, request, pk=None):
        """Queue a new build for this template, with optional node affinity pre-check."""
        from ..validators import NodeAffinityValidator

        template = self.get_object()
        skip_validation = request.data.get("skip_node_validation", False)

        if not skip_validation:
            validator = NodeAffinityValidator(template)
            is_valid, errors, warnings = validator.validate()
            if not is_valid:
                return Response(
                    {"errors": errors, "warnings": warnings},
                    status=http_status.HTTP_409_CONFLICT,
                )

        build = models.PackerBuild.objects.create(
            template=template,
            triggered_by=str(request.user),
            variable_overrides=request.data.get("variable_overrides", {}),
            status="queued",
        )
        models.PackerTemplate.objects.filter(pk=template.pk).update(build_status="building")

        from ..jobs import dispatch_build

        dispatch_build(build)
        serializer = PackerBuildSerializer(build, context={"request": request})
        return Response(serializer.data, status=http_status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def builds(self, request, pk=None):
        """List all builds for this template, newest first."""
        template = self.get_object()
        builds = models.PackerBuild.objects.filter(template=template).order_by("-queued_at")
        serializer = PackerBuildSerializer(builds, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="validate-node")
    def validate_node(self, request, pk=None):
        """Run node affinity validation for this template."""
        from ..validators import NodeAffinityValidator

        template = self.get_object()
        validator = NodeAffinityValidator(template)
        is_valid, errors, warnings = validator.validate()
        status_code = http_status.HTTP_200_OK if is_valid else http_status.HTTP_409_CONFLICT
        return Response(
            {"valid": is_valid, "errors": errors, "warnings": warnings},
            status=status_code,
        )


class PackerBuildViewSet(NetBoxModelViewSet):
    queryset = models.PackerBuild.objects.select_related("template").prefetch_related("tags")
    serializer_class = PackerBuildSerializer
    filterset_class = filtersets.PackerBuildFilterSet

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel a queued or running build."""
        build = self.get_object()
        if build.status not in ("queued", "running"):
            return Response(
                {"detail": f"Cannot cancel a build with status '{build.status}'."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        from django.utils import timezone

        build.status = "cancelled"
        build.finished_at = timezone.now()
        build.save(update_fields=["status", "finished_at"])
        active = models.PackerBuild.objects.filter(template=build.template, status__in=("queued", "running")).exists()
        if not active:
            models.PackerTemplate.objects.filter(pk=build.template_id).update(build_status="ready")
        serializer = self.get_serializer(build)
        return Response(serializer.data)


class PackerBuildTargetViewSet(NetBoxModelViewSet):
    queryset = models.PackerBuildTarget.objects.select_related(
        "template",
    ).prefetch_related("tags")
    serializer_class = PackerBuildTargetSerializer
    filterset_class = filtersets.PackerBuildTargetFilterSet
