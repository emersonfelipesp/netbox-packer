from django.shortcuts import get_object_or_404
from netbox.api.authentication import TokenPermissions
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import filtersets, models
from ..influxdb_profiles import INFLUXDB_PROFILES
from .serializers import (
    PackerBuildSerializer,
    PackerBuildTargetSerializer,
    PackerInstallerConfigSerializer,
    PackerTemplateBuildRequestSerializer,
    PackerTemplateSerializer,
)


class PackerTemplateBuildPermissions(TokenPermissions):
    """Map the mutating build action to change permission, never HTTP POST/add."""

    perms_map = {
        **TokenPermissions.perms_map,
        "POST": ["%(app_label)s.change_%(model_name)s"],
    }


class PackerBuildCancelPermissions(TokenPermissions):
    """Map cancellation to change permission on the existing build."""

    perms_map = {
        **TokenPermissions.perms_map,
        "POST": ["%(app_label)s.change_%(model_name)s"],
    }


class PackerInstallerConfigViewSet(NetBoxModelViewSet):
    queryset = models.PackerInstallerConfig.objects.prefetch_related("tags")
    serializer_class = PackerInstallerConfigSerializer
    filterset_class = filtersets.PackerInstallerConfigFilterSet


class InfluxDBProfileListView(APIView):
    """Expose supported InfluxDB profiles joined to their seeded template rows."""

    def get(self, request):
        if not request.user.has_perm("netbox_packer.view_packertemplate"):
            raise PermissionDenied("view_packertemplate permission is required.")
        template_names = [profile["template_name"] for profile in INFLUXDB_PROFILES]
        templates = {
            template.name: template for template in models.PackerTemplate.objects.filter(name__in=template_names)
        }
        profiles = []
        for profile in INFLUXDB_PROFILES:
            template = templates.get(profile["template_name"])
            profiles.append(
                {
                    **profile,
                    "template_id": template.pk if template else None,
                    "build_status": template.build_status if template else "missing",
                    "ready": bool(template and template.build_status == "ready"),
                }
            )
        return Response({"profiles": profiles})


class PackerTemplateViewSet(NetBoxModelViewSet):
    queryset = models.PackerTemplate.objects.select_related(
        "installer_config",
    ).prefetch_related("tags")
    serializer_class = PackerTemplateSerializer
    filterset_class = filtersets.PackerTemplateFilterSet

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[PackerTemplateBuildPermissions],
    )
    def build(self, request, pk=None):
        """Queue a new build for this template, with optional node affinity pre-check."""
        from ..validators import NodeAffinityValidator

        if not request.user.has_perm("netbox_packer.change_packertemplate"):
            raise PermissionDenied("change_packertemplate permission is required.")
        template = get_object_or_404(
            models.PackerTemplate.objects.restrict(request.user, "change").select_related("installer_config"),
            pk=pk,
        )
        request_serializer = PackerTemplateBuildRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        variable_overrides = request_serializer.validated_data["variable_overrides"]
        endpoint_id = variable_overrides.get("endpoint_id")
        target_node = variable_overrides.get("target_node")
        installer = template.installer_config
        is_cloud_config = installer is not None and installer.installer_type == "cloud_config"
        endpoint_agnostic = not template.proxmox_endpoint or template.proxmox_node == "select-at-build"
        if (is_cloud_config or endpoint_agnostic) and (not endpoint_id or not target_node):
            return Response(
                {
                    "detail": (
                        "Cloud or endpoint-agnostic templates require positive "
                        "variable_overrides.endpoint_id and variable_overrides.target_node."
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        skip_validation = request_serializer.validated_data["skip_node_validation"]
        selector_is_explicit = bool(endpoint_id and target_node)
        if is_cloud_config and not models.PackerPluginSettings.get_solo().proxbox_writes_enabled:
            return Response(
                {"detail": "Proxbox writes are disabled by the operator safety gate."},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not skip_validation and not selector_is_explicit:
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
            variable_overrides=variable_overrides,
            status="queued",
        )
        models.PackerTemplate.objects.filter(pk=template.pk).update(build_status="building")

        from ..jobs import dispatch_build

        try:
            dispatch_build(build)
        except Exception as exc:
            serializer = PackerBuildSerializer(build, context={"request": request})
            return Response(
                {
                    "detail": f"Build #{build.pk} could not be queued: {exc}",
                    "build": serializer.data,
                },
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )

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

    def create(self, request, *args, **kwargs):
        """Build records are created only through the selector-aware template action."""
        return Response(
            {"detail": "PackerBuild records cannot be created through the generic endpoint."},
            status=http_status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def update(self, request, *args, **kwargs):
        """Execution records and their input snapshots are immutable."""
        return Response(
            {"detail": "PackerBuild records are immutable."},
            status=http_status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Preserve immutable execution history."""
        return Response(
            {"detail": "PackerBuild records cannot be deleted through the generic endpoint."},
            status=http_status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[PackerBuildCancelPermissions],
    )
    def cancel(self, request, pk=None):
        """Atomically cancel only queued work; running execution is not interruptible here."""
        if not request.user.has_perm("netbox_packer.change_packerbuild"):
            raise PermissionDenied("change_packerbuild permission is required.")
        build = get_object_or_404(
            models.PackerBuild.objects.restrict(request.user, "change").select_related("template"),
            pk=pk,
        )
        if build.status != "queued":
            return Response(
                {"detail": f"Only queued builds can be cancelled; current status is '{build.status}'."},
                status=http_status.HTTP_409_CONFLICT,
            )
        from django.utils import timezone

        finished_at = timezone.now()
        cancelled = models.PackerBuild.objects.filter(pk=build.pk, status="queued").update(
            status="cancelled",
            finished_at=finished_at,
        )
        if not cancelled:
            build.refresh_from_db(fields=["status"])
            return Response(
                {"detail": f"Cancellation lost the start race; current status is '{build.status}'."},
                status=http_status.HTTP_409_CONFLICT,
            )
        build.status = "cancelled"
        build.finished_at = finished_at
        active = models.PackerBuild.objects.filter(template=build.template, status__in=("queued", "running")).exists()
        if not active:
            replacement_status = "ready" if build.template.built_at else "pending"
            models.PackerTemplate.objects.filter(pk=build.template_id).update(build_status=replacement_status)
        serializer = self.get_serializer(build)
        return Response(serializer.data)


class PackerBuildTargetViewSet(NetBoxModelViewSet):
    queryset = models.PackerBuildTarget.objects.select_related(
        "template",
    ).prefetch_related("tags")
    serializer_class = PackerBuildTargetSerializer
    filterset_class = filtersets.PackerBuildTargetFilterSet
