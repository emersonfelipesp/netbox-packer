from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from netbox.views import generic
from utilities.views import register_model_view

from . import filtersets, forms, models, tables

# ── PackerInstallerConfig ─────────────────────────────────────────────────────


@register_model_view(models.PackerInstallerConfig)
class PackerInstallerConfigView(generic.ObjectView):
    queryset = models.PackerInstallerConfig.objects.prefetch_related("tags")


@register_model_view(models.PackerInstallerConfig, name="list", path="", detail=False)
class PackerInstallerConfigListView(generic.ObjectListView):
    queryset = models.PackerInstallerConfig.objects.prefetch_related("tags")
    table = tables.PackerInstallerConfigTable
    filterset = filtersets.PackerInstallerConfigFilterSet
    filterset_form = forms.PackerInstallerConfigFilterForm


@register_model_view(models.PackerInstallerConfig, name="add", detail=False)
@register_model_view(models.PackerInstallerConfig, name="edit")
class PackerInstallerConfigEditView(generic.ObjectEditView):
    queryset = models.PackerInstallerConfig.objects.all()
    form = forms.PackerInstallerConfigForm


@register_model_view(models.PackerInstallerConfig, name="delete")
class PackerInstallerConfigDeleteView(generic.ObjectDeleteView):
    queryset = models.PackerInstallerConfig.objects.all()


# ── PackerTemplate ────────────────────────────────────────────────────────────


@register_model_view(models.PackerTemplate)
class PackerTemplateView(generic.ObjectView):
    queryset = models.PackerTemplate.objects.select_related(
        "installer_config",
    ).prefetch_related("tags", "builds", "build_targets")

    def get_extra_context(self, request, instance):
        builds_table = tables.PackerBuildTable(
            instance.builds.all()[:10],
        )
        builds_table.configure(request)
        targets_table = tables.PackerBuildTargetTable(
            instance.build_targets.all(),
        )
        targets_table.configure(request)
        return {
            "builds_table": builds_table,
            "targets_table": targets_table,
        }


@register_model_view(models.PackerTemplate, name="list", path="", detail=False)
class PackerTemplateListView(generic.ObjectListView):
    queryset = models.PackerTemplate.objects.select_related(
        "installer_config",
    ).prefetch_related("tags")
    table = tables.PackerTemplateTable
    filterset = filtersets.PackerTemplateFilterSet
    filterset_form = forms.PackerTemplateFilterForm


@register_model_view(models.PackerTemplate, name="add", detail=False)
@register_model_view(models.PackerTemplate, name="edit")
class PackerTemplateEditView(generic.ObjectEditView):
    queryset = models.PackerTemplate.objects.all()
    form = forms.PackerTemplateForm


@register_model_view(models.PackerTemplate, name="delete")
class PackerTemplateDeleteView(generic.ObjectDeleteView):
    queryset = models.PackerTemplate.objects.all()


@register_model_view(models.PackerTemplate, name="build", path="build/")
class PackerTemplateBuildView(generic.ObjectView):
    """Queue a new build for a PackerTemplate and redirect to the new build."""

    queryset = models.PackerTemplate.objects.all()

    def post(self, request, pk):
        template = get_object_or_404(models.PackerTemplate, pk=pk)
        build = models.PackerBuild.objects.create(
            template=template,
            triggered_by=str(request.user),
            status="queued",
        )
        models.PackerTemplate.objects.filter(pk=template.pk).update(build_status="building")

        from .jobs import dispatch_build

        try:
            dispatch_build(build)
        except Exception as exc:
            messages.error(
                request,
                f"Build #{build.pk} could not be queued for template '{template.name}': {exc}",
            )
            return redirect(build.get_absolute_url())

        messages.success(
            request,
            f"Build #{build.pk} queued for template '{template.name}'.",
        )
        return redirect(build.get_absolute_url())


@register_model_view(models.PackerTemplate, name="create_instance", path="create-instance/")
class PackerTemplateCreateInstanceView(generic.ObjectView):
    """Create a VM from a PackerTemplate by delegating to proxbox-api."""

    queryset = models.PackerTemplate.objects.all()

    def post(self, request, pk):
        template = get_object_or_404(models.PackerTemplate.objects.all(), pk=pk)
        if not request.user.has_perm("netbox_packer.change_packertemplate"):
            raise PermissionDenied

        form = forms.PackerTemplateCreateInstanceForm(request.POST, template=template)
        if not form.is_valid():
            messages.error(
                request,
                "Could not create instance: "
                + "; ".join(f"{field}: {', '.join(errors)}" for field, errors in form.errors.items()),
            )
            return _redirect_after_instance_create(request, template)

        settings_row = models.PackerPluginSettings.get_solo()
        proxbox_api_url = (settings_row.proxbox_api_url or "").strip()
        proxbox_api_key = settings_row.get_proxbox_api_key()
        if not proxbox_api_url or not proxbox_api_key:
            messages.error(
                request,
                "Configure Packer plugin proxbox_api_url and API key before creating VM instances.",
            )
            return _redirect_after_instance_create(request, template)

        from .proxbox_client import ProxboxApiError, call_proxbox_vm_provision

        payload = form.proxbox_payload(template)
        try:
            response = call_proxbox_vm_provision(
                proxbox_api_url=proxbox_api_url,
                proxbox_api_key=proxbox_api_key,
                **payload,
            )
        except ProxboxApiError as exc:
            messages.error(request, f"proxbox-api failed to create the instance: {exc}")
            return _redirect_after_instance_create(request, template)

        status = response.get("status") or "submitted"
        messages.success(
            request,
            f"Create request submitted for VMID {payload['new_vmid']} from template '{template.name}' ({status}).",
        )
        return _redirect_after_instance_create(request, template)


def _redirect_after_instance_create(request, template):
    """Return to the table when possible, falling back to the template detail page."""
    return_url = (request.POST.get("return_url") or "").strip()
    if return_url.startswith("/"):
        return redirect(return_url)
    return redirect(template.get_absolute_url())


# ── PackerBuild ───────────────────────────────────────────────────────────────


@register_model_view(models.PackerBuild)
class PackerBuildView(generic.ObjectView):
    queryset = models.PackerBuild.objects.select_related("template").prefetch_related("tags")


@register_model_view(models.PackerBuild, name="list", path="", detail=False)
class PackerBuildListView(generic.ObjectListView):
    queryset = models.PackerBuild.objects.select_related("template").prefetch_related("tags")
    table = tables.PackerBuildTable
    filterset = filtersets.PackerBuildFilterSet
    filterset_form = forms.PackerBuildFilterForm


@register_model_view(models.PackerBuild, name="add", detail=False)
@register_model_view(models.PackerBuild, name="edit")
class PackerBuildEditView(generic.ObjectEditView):
    queryset = models.PackerBuild.objects.all()
    form = forms.PackerBuildForm


@register_model_view(models.PackerBuild, name="delete")
class PackerBuildDeleteView(generic.ObjectDeleteView):
    queryset = models.PackerBuild.objects.all()


# ── PackerBuildTarget ─────────────────────────────────────────────────────────


@register_model_view(models.PackerBuildTarget)
class PackerBuildTargetView(generic.ObjectView):
    queryset = models.PackerBuildTarget.objects.select_related(
        "template",
    ).prefetch_related("tags")


@register_model_view(models.PackerBuildTarget, name="list", path="", detail=False)
class PackerBuildTargetListView(generic.ObjectListView):
    queryset = models.PackerBuildTarget.objects.select_related(
        "template",
    ).prefetch_related("tags")
    table = tables.PackerBuildTargetTable
    filterset = filtersets.PackerBuildTargetFilterSet
    filterset_form = forms.PackerBuildTargetFilterForm


@register_model_view(models.PackerBuildTarget, name="add", detail=False)
@register_model_view(models.PackerBuildTarget, name="edit")
class PackerBuildTargetEditView(generic.ObjectEditView):
    queryset = models.PackerBuildTarget.objects.all()
    form = forms.PackerBuildTargetForm


@register_model_view(models.PackerBuildTarget, name="delete")
class PackerBuildTargetDeleteView(generic.ObjectDeleteView):
    queryset = models.PackerBuildTarget.objects.all()
