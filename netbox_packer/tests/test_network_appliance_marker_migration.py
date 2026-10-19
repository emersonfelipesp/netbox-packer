"""Database-backed tests for the network appliance marker seed migration."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase

from netbox_packer.models import PackerTemplate

migration = import_module("netbox_packer.migrations.0034_seed_network_appliance_marker")


def _schema_editor():
    def execute(statement):
        with connection.cursor() as cursor:
            cursor.execute(statement)

    return SimpleNamespace(
        connection=connection,
        execute=execute,
        quote_name=connection.ops.quote_name,
    )


def _template_data(**overrides):
    data = {
        "name": "unrelated-template",
        "os_family": "ubuntu",
        "os_version": "24.04",
        "proxmox_template_id": 9900,
        "proxmox_node": "pve01",
        "build_status": "pending",
    }
    data.update(overrides)
    return data


class NetworkApplianceMarkerMigrationTest(TestCase):
    def setUp(self):
        PackerTemplate.objects.filter(name=migration.TEMPLATE_NAME).delete()

    def test_clean_apply_creates_row_with_marker_and_no_installer_config(self):
        migration.seed_network_appliance_marker(django_apps, _schema_editor())

        template = PackerTemplate.objects.get(name=migration.TEMPLATE_NAME)
        self.assertEqual(template.provisions_service, migration.SERVICE_MARKER)
        self.assertIsNone(template.installer_config_id)
        self.assertEqual(template.proxmox_template_id, migration.PLACEHOLDER_VMID)
        self.assertEqual(template.proxmox_node, migration.PROXMOX_NODE)
        self.assertFalse(template.install_qemu_guest_agent)
        self.assertFalse(template.install_zabbix_agent2)

    def test_existing_row_with_empty_marker_gets_marker_and_other_fields_untouched(self):
        existing = PackerTemplate.objects.create(
            **_template_data(
                name=migration.TEMPLATE_NAME,
                os_family="debian",
                os_version="13",
                proxmox_template_id=migration.TEMPLATE_VMID,
                proxmox_node="operator-node",
                description="operator-owned description",
                provisions_service="",
            )
        )

        migration.seed_network_appliance_marker(django_apps, _schema_editor())

        existing.refresh_from_db()
        self.assertEqual(existing.provisions_service, migration.SERVICE_MARKER)
        self.assertEqual(existing.proxmox_template_id, migration.TEMPLATE_VMID)
        self.assertEqual(existing.proxmox_node, "operator-node")
        self.assertEqual(existing.description, "operator-owned description")

    def test_existing_row_with_different_marker_raises_and_stays_untouched(self):
        existing = PackerTemplate.objects.create(
            **_template_data(
                name=migration.TEMPLATE_NAME,
                os_family="debian",
                os_version="13",
                provisions_service="other-service",
            )
        )

        with self.assertRaisesRegex(RuntimeError, "naming collision.*Rename the existing row"):
            migration.seed_network_appliance_marker(django_apps, _schema_editor())

        existing.refresh_from_db()
        self.assertEqual(existing.provisions_service, "other-service")

    def test_existing_row_with_different_identity_raises_and_stays_untouched(self):
        existing = PackerTemplate.objects.create(
            **_template_data(
                name=migration.TEMPLATE_NAME,
                os_family="ubuntu",
                os_version="24.04",
                proxmox_template_id=9999,
                provisions_service="",
            )
        )

        with self.assertRaisesRegex(RuntimeError, "os_family, os_version, proxmox_template_id"):
            migration.seed_network_appliance_marker(django_apps, _schema_editor())

        existing.refresh_from_db()
        self.assertEqual(existing.provisions_service, "")

    def test_unrelated_template_is_untouched(self):
        unrelated = PackerTemplate.objects.create(**_template_data(provisions_service=""))

        migration.seed_network_appliance_marker(django_apps, _schema_editor())

        unrelated.refresh_from_db()
        self.assertEqual(unrelated.provisions_service, "")
        self.assertEqual(unrelated.name, "unrelated-template")
        self.assertTrue(PackerTemplate.objects.filter(name=migration.TEMPLATE_NAME).exists())

    def test_forward_is_idempotent_when_applied_twice(self):
        migration.seed_network_appliance_marker(django_apps, _schema_editor())
        template = PackerTemplate.objects.get(name=migration.TEMPLATE_NAME)
        original_pk = template.pk
        original_vmid = template.proxmox_template_id

        migration.seed_network_appliance_marker(django_apps, _schema_editor())

        template.refresh_from_db()
        self.assertEqual(template.pk, original_pk)
        self.assertEqual(template.provisions_service, migration.SERVICE_MARKER)
        self.assertEqual(template.proxmox_template_id, original_vmid)
        self.assertEqual(PackerTemplate.objects.filter(name=migration.TEMPLATE_NAME).count(), 1)

    def test_old_migration_record_adopts_operator_edited_row(self):
        existing = PackerTemplate.objects.create(
            **_template_data(
                name=migration.TEMPLATE_NAME,
                os_family="ubuntu",
                os_version="24.04",
                proxmox_template_id=9999,
                proxmox_node="operator-node",
                description="operator-owned description",
                provisions_service="operator-service",
            )
        )

        with patch.object(migration, "MigrationRecorder") as recorder:
            recorder.return_value.applied_migrations.return_value = {migration.PREVIOUS_MIGRATION: object()}
            migration.seed_network_appliance_marker(django_apps, _schema_editor())

        existing.refresh_from_db()
        self.assertEqual(existing.proxmox_template_id, 9999)
        self.assertEqual(existing.proxmox_node, "operator-node")
        self.assertEqual(existing.description, "operator-owned description")
        self.assertEqual(existing.provisions_service, "operator-service")
