"""Runtime GraphQL contracts for sensitive PackerBuild fields.

Runs under NetBox: ``python manage.py test netbox_packer``.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from netbox_packer.api.serializers import PackerBuildSerializer, PackerTemplateBuildRequestSerializer
from netbox_packer.graphql.types import PackerBuildType
from netbox_packer.models import PackerBuild
from netbox_packer.security import REDACTED_LEGACY_PROXBOX_LOG


class PackerBuildGraphQLSecurityTest(SimpleTestCase):
    def test_raw_persistence_fields_are_absent_from_generated_type(self):
        field_names = {field.python_name for field in PackerBuildType.__strawberry_definition__.fields}

        self.assertNotIn("variable_overrides", field_names)
        self.assertNotIn("log", field_names)

    def test_model_read_helpers_recursively_redact_legacy_values(self):
        canary = "runtime-model-signed-url-canary-not-a-secret"
        build = PackerBuild(
            variable_overrides={
                "source": {"image_url": f"https://images.example/image.qcow2?token={canary}"},
                "target_node": "pve01",
            },
            log=(
                "[INFO] Cloud-init template image build for 'legacy'\n"
                f"[INFO] Base image: https://images.example/image.qcow2?token={canary}"
            ),
        )

        self.assertEqual(
            build.safe_variable_overrides,
            {"source": {}, "target_node": "pve01"},
        )
        self.assertNotIn(canary, repr(build.safe_variable_overrides))
        self.assertEqual(build.safe_log, REDACTED_LEGACY_PROXBOX_LOG)

    def test_objectchange_snapshot_omits_raw_override_and_log_fields(self):
        canary = "runtime-objectchange-signed-url-canary-not-a-secret"
        build = PackerBuild(
            pk=31,
            template_id=7,
            variable_overrides={"source": {"image_url": f"https://images.example/image.qcow2?token={canary}"}},
            log=f"legacy output {canary}",
        )
        build._tags = [SimpleNamespace(name="test-tag")]

        snapshot = build.serialize_object()

        self.assertNotIn("variable_overrides", snapshot)
        self.assertNotIn("log", snapshot)
        self.assertNotIn(canary, repr(snapshot))

    def test_build_action_serializer_rejects_nested_image_url_without_reflection(self):
        canary = "runtime-serializer-signed-url-canary-not-a-secret"
        serializer = PackerTemplateBuildRequestSerializer(
            data={"variable_overrides": {"source": {"image_url": f"https://images.example/image.qcow2?token={canary}"}}}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("variable_overrides", serializer.errors)
        self.assertNotIn(canary, repr(serializer.errors))

    def test_public_build_serializer_rejects_worker_owned_fields_without_reflection(self):
        canary = "runtime-rest-log-canary-not-a-secret"
        build = PackerBuild(status="queued", log="safe existing log", variable_overrides={})
        serializer = PackerBuildSerializer(
            instance=build,
            data={
                "log": canary,
                "status": "success",
                "variable_overrides": {"nested": {"token": canary}},
            },
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"log", "status", "variable_overrides"})
        self.assertNotIn(canary, repr(serializer.errors))
        self.assertEqual(build.log, "safe existing log")
        self.assertEqual(build.status, "queued")
        self.assertEqual(build.variable_overrides, {})
