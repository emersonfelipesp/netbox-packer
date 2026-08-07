"""Database-backed collision tests for the Akvorado seed migration."""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from netbox_packer.models import PackerInstallerConfig, PackerTemplate

migration = import_module("netbox_packer.migrations.0024_seed_akvorado_cloud_init")


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


class AkvoradoSeedMigrationTest(TestCase):
    def setUp(self):
        PackerTemplate.objects.filter(name=migration.TEMPLATE_NAME).delete()
        PackerInstallerConfig.objects.filter(
            name=migration.CONFIG_NAME,
            version=migration.CONFIG_VERSION,
        ).delete()

    def test_clean_apply_creates_seed_rows(self):
        migration.seed_akvorado(django_apps, None)

        config = PackerInstallerConfig.objects.get(
            name=migration.CONFIG_NAME,
            version=migration.CONFIG_VERSION,
        )
        template = PackerTemplate.objects.get(name=migration.TEMPLATE_NAME)
        self.assertEqual(config.content, migration.AKVORADO_CLOUD_CONFIG)
        self.assertEqual(template.proxmox_template_id, migration.TEMPLATE_VMID)
        self.assertEqual(template.provisions_service, "akvorado")
        self.assertEqual(template.installer_config_id, config.pk)

    def test_colliding_installer_config_fails_without_overwriting(self):
        collision = PackerInstallerConfig.objects.create(
            name=migration.CONFIG_NAME,
            version=migration.CONFIG_VERSION,
            os_family="ubuntu",
            installer_type="cloud_config",
            content="#cloud-config\n# operator-owned content\n",
            description="operator-owned",
        )

        with self.assertRaisesRegex(RuntimeError, "naming collision.*Rename the existing row"):
            migration.seed_akvorado(django_apps, None)

        collision.refresh_from_db()
        self.assertEqual(collision.content, "#cloud-config\n# operator-owned content\n")
        self.assertEqual(collision.description, "operator-owned")
        self.assertFalse(PackerTemplate.objects.filter(name=migration.TEMPLATE_NAME).exists())

    def test_referenced_colliding_config_fails_without_affecting_reference(self):
        collision = PackerInstallerConfig.objects.create(
            name=migration.CONFIG_NAME,
            version=migration.CONFIG_VERSION,
            os_family="ubuntu",
            installer_type="cloud_config",
            content="#cloud-config\n# shared operator-owned content\n",
        )
        referencing_template = PackerTemplate.objects.create(**_template_data(installer_config=collision))

        with self.assertRaisesRegex(RuntimeError, "naming collision.*Rename the existing row"):
            migration.seed_akvorado(django_apps, None)

        collision.refresh_from_db()
        referencing_template.refresh_from_db()
        self.assertEqual(collision.content, "#cloud-config\n# shared operator-owned content\n")
        self.assertEqual(referencing_template.installer_config_id, collision.pk)

    def test_colliding_template_vmid_fails_without_overwriting(self):
        migration.seed_akvorado(django_apps, None)
        config = PackerInstallerConfig.objects.get(
            name=migration.CONFIG_NAME,
            version=migration.CONFIG_VERSION,
        )
        PackerTemplate.objects.get(name=migration.TEMPLATE_NAME).delete()
        collision = PackerTemplate.objects.create(
            **_template_data(
                name=migration.TEMPLATE_NAME,
                proxmox_template_id=9999,
                installer_config=config,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "naming collision.*Rename the existing row"):
            migration.seed_akvorado(django_apps, None)

        collision.refresh_from_db()
        self.assertEqual(collision.proxmox_template_id, 9999)
        self.assertEqual(collision.installer_config_id, config.pk)
