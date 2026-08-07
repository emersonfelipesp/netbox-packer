"""NetBox-runtime authorization tests for the mutating template build action."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from core.models import ObjectType
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from users.models import ObjectPermission
from utilities.testing import APITestCase

from netbox_packer.jobs import PackerBuildJob, PackerStalenessCheckJob
from netbox_packer.models import PackerBuild, PackerInstallerConfig, PackerTemplate


class PackerTemplateBuildPermissionTest(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.template = PackerTemplate.objects.create(
            name="runtime-build-permission-template",
            os_family="ubuntu",
            os_version="24.04",
            proxmox_template_id=9910,
            proxmox_endpoint="https://pve.example.invalid:8006",
            proxmox_node="pve01",
        )

    def _post(self):
        return self.client.post(
            reverse(
                "plugins-api:netbox_packer-api:packertemplate-build",
                kwargs={"pk": self.template.pk},
            ),
            {"variable_overrides": {"target_node": "pve01"}},
            format="json",
            **self.header,
        )

    def test_add_only_user_cannot_dispatch_or_mutate_template(self) -> None:
        self.add_permissions("netbox_packer.add_packertemplate")

        with patch("netbox_packer.jobs.dispatch_build") as dispatch:
            response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PackerBuild.objects.filter(template=self.template).exists())
        self.template.refresh_from_db()
        self.assertNotEqual(self.template.build_status, "building")
        dispatch.assert_not_called()

    def test_view_only_user_cannot_dispatch_or_mutate_template(self) -> None:
        self.add_permissions("netbox_packer.view_packertemplate")

        with patch("netbox_packer.jobs.dispatch_build") as dispatch:
            response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PackerBuild.objects.filter(template=self.template).exists())
        self.template.refresh_from_db()
        self.assertNotEqual(self.template.build_status, "building")
        dispatch.assert_not_called()

    def test_change_user_can_dispatch(self) -> None:
        self.add_permissions("netbox_packer.change_packertemplate")

        with patch("netbox_packer.jobs.dispatch_build") as dispatch:
            response = self._post()

        self.assertEqual(response.status_code, 202)
        build = PackerBuild.objects.get(template=self.template)
        dispatch.assert_called_once_with(build)
        self.template.refresh_from_db()
        self.assertEqual(self.template.build_status, "building")


class CloudTemplateAutomaticRebuildTest(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        installer = PackerInstallerConfig.objects.create(
            name="runtime-cloud-selector-required",
            os_family="ubuntu",
            installer_type="cloud_config",
            content="#cloud-config\n",
        )
        cls.template = PackerTemplate.objects.create(
            name="runtime-cloud-auto-rebuild-template",
            os_family="ubuntu",
            os_version="24.04",
            proxmox_template_id=9911,
            proxmox_node="select-at-build",
            installer_config=installer,
            built_at=timezone.now() - timedelta(days=5),
            max_age_days=1,
            auto_rebuild=True,
        )

    def test_management_command_marks_stale_but_does_not_queue_unbound_cloud_build(self) -> None:
        output = StringIO()

        call_command("check_packer_staleness", stdout=output)

        self.assertFalse(PackerBuild.objects.filter(template=self.template).exists())
        self.template.refresh_from_db()
        self.assertEqual(self.template.build_status, "stale")
        self.assertIn("require API-supplied endpoint_id and target_node", output.getvalue())

    def test_scheduled_job_marks_stale_but_does_not_queue_unbound_cloud_build(self) -> None:
        # ``run()`` does not use JobRunner instance state; call the unbound
        # method so this unit remains about staleness behavior rather than
        # constructing NetBox's scheduler-owned core.Job wrapper.
        PackerStalenessCheckJob.run(object())

        self.assertFalse(PackerBuild.objects.filter(template=self.template).exists())
        self.template.refresh_from_db()
        self.assertEqual(self.template.build_status, "stale")


class PackerHtmlObjectPermissionTest(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.allowed_template = PackerTemplate.objects.create(
            name="html-object-permission-allowed",
            os_family="ubuntu",
            os_version="24.04",
            proxmox_template_id=9920,
            proxmox_endpoint="https://pve.example.invalid:8006",
            proxmox_node="pve01",
        )
        cls.denied_template = PackerTemplate.objects.create(
            name="html-object-permission-denied",
            os_family="ubuntu",
            os_version="24.04",
            proxmox_template_id=9921,
            proxmox_endpoint="https://pve.example.invalid:8006",
            proxmox_node="pve01",
        )

    def _grant_constrained_template_permission(self, action: str, template: PackerTemplate) -> None:
        permission = ObjectPermission.objects.create(
            name=f"runtime-{action}-{template.pk}",
            actions=[action],
            constraints={"id": template.pk},
        )
        permission.object_types.set([ObjectType.objects.get_for_model(PackerTemplate)])
        permission.users.set([self.user])

    def test_build_post_cannot_escape_constrained_change_queryset(self) -> None:
        self._grant_constrained_template_permission("change", self.allowed_template)
        self._grant_constrained_template_permission("view", self.denied_template)
        self.client.force_login(self.user)

        with patch("netbox_packer.jobs.dispatch_build") as dispatch:
            response = self.client.post(
                reverse(
                    "plugins:netbox_packer:packertemplate_build",
                    kwargs={"pk": self.denied_template.pk},
                )
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(PackerBuild.objects.filter(template=self.denied_template).exists())
        dispatch.assert_not_called()

    def test_create_instance_post_cannot_escape_constrained_change_queryset(self) -> None:
        self._grant_constrained_template_permission("change", self.allowed_template)
        self._grant_constrained_template_permission("view", self.denied_template)
        self.client.force_login(self.user)

        with patch("netbox_packer.proxbox_client.call_proxbox_vm_provision") as provision:
            response = self.client.post(
                reverse(
                    "plugins:netbox_packer:packertemplate_create_instance",
                    kwargs={"pk": self.denied_template.pk},
                )
            )

        self.assertEqual(response.status_code, 404)
        provision.assert_not_called()


class PackerBuildMutationAndCancellationTest(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.template = PackerTemplate.objects.create(
            name="runtime-cancel-template",
            os_family="ubuntu",
            os_version="24.04",
            proxmox_template_id=9930,
            proxmox_endpoint="https://pve.example.invalid:8006",
            proxmox_node="pve01",
            build_status="building",
        )

    def _build(self, *, status: str = "queued") -> PackerBuild:
        return PackerBuild.objects.create(template=self.template, status=status, variable_overrides={})

    def _cancel(self, build: PackerBuild):
        return self.client.post(
            reverse(
                "plugins-api:netbox_packer-api:packerbuild-cancel",
                kwargs={"pk": build.pk},
            ),
            {},
            format="json",
            **self.header,
        )

    def test_add_only_user_cannot_cancel_existing_build(self) -> None:
        build = self._build()
        self.add_permissions("netbox_packer.add_packerbuild")

        response = self._cancel(build)

        self.assertEqual(response.status_code, 403)
        build.refresh_from_db()
        self.assertEqual(build.status, "queued")

    def test_cancelled_queue_row_is_never_claimed_by_worker(self) -> None:
        build = self._build()
        self.add_permissions("netbox_packer.change_packerbuild")

        response = self._cancel(build)

        self.assertEqual(response.status_code, 200)
        build.refresh_from_db()
        self.assertEqual(build.status, "cancelled")
        with patch.object(PackerBuildJob, "_run_packer") as execute:
            PackerBuildJob.run(object(), build_id=build.pk)
        execute.assert_not_called()

    def test_running_build_cancellation_returns_conflict_without_state_change(self) -> None:
        build = self._build(status="running")
        self.add_permissions("netbox_packer.change_packerbuild")

        response = self._cancel(build)

        self.assertEqual(response.status_code, 409)
        build.refresh_from_db()
        self.assertEqual(build.status, "running")

    def test_cancel_respects_constrained_build_change_permission(self) -> None:
        allowed = self._build()
        denied = self._build()
        permission = ObjectPermission.objects.create(
            name="runtime-constrained-build-cancel",
            actions=["change"],
            constraints={"id": allowed.pk},
        )
        permission.object_types.set([ObjectType.objects.get_for_model(PackerBuild)])
        permission.users.set([self.user])

        response = self._cancel(denied)

        self.assertEqual(response.status_code, 404)
        denied.refresh_from_db()
        self.assertEqual(denied.status, "queued")

    def test_generic_create_and_retarget_are_disabled(self) -> None:
        build = self._build()
        self.add_permissions("netbox_packer.add_packerbuild", "netbox_packer.change_packerbuild")
        collection_url = reverse("plugins-api:netbox_packer-api:packerbuild-list")
        detail_url = reverse(
            "plugins-api:netbox_packer-api:packerbuild-detail",
            kwargs={"pk": build.pk},
        )

        create_response = self.client.post(
            collection_url,
            {"template": {"id": self.template.pk}, "variable_overrides": {"target_node": "other"}},
            format="json",
            **self.header,
        )
        patch_response = self.client.patch(
            detail_url,
            {"template": {"id": self.template.pk}, "variable_overrides": {"target_node": "other"}},
            format="json",
            **self.header,
        )

        self.assertEqual(create_response.status_code, 405)
        self.assertEqual(patch_response.status_code, 405)
        build.refresh_from_db()
        self.assertEqual(build.variable_overrides, {})
