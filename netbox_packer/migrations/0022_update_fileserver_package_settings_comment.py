"""Correct the File Server package-index credential rotation instructions."""

import hashlib

from django.db import migrations

CONFIG_NAME = "fileserver-allinone-cloud-config"
CONFIG_VERSION = "1.0.1"

STALE_PIP_CONF_COMMENT = """      # netbox-packer replaces these placeholders from its service environment.
      # Operators rotate NMS_FILESERVER_PACKAGE_READ_TOKEN there and rebake VMID
      # 9300; use only a dedicated non-human Gitea package-Read token, never a
      # personal token or PACKAGE_WRITE_TOKEN."""

SETTINGS_PIP_CONF_COMMENT = """      # netbox-packer replaces these placeholders from PackerPluginSettings.
      # Operators update fileserver_package_read_user and rotate the token with
      # set_fileserver_package_read_token(), then rebake VMID 9300; use only a
      # dedicated non-human Gitea package-Read token, never a personal token or
      # PACKAGE_WRITE_TOKEN."""


def update_fileserver_package_settings_comment(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config = PackerInstallerConfig.objects.filter(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
    ).first()
    if config is None:
        return

    updated_content = config.content.replace(
        STALE_PIP_CONF_COMMENT,
        SETTINGS_PIP_CONF_COMMENT,
        1,
    )
    if updated_content == config.content:
        return

    PackerInstallerConfig.objects.filter(pk=config.pk).update(
        content=updated_content,
        checksum=hashlib.sha256(updated_content.encode()).hexdigest(),
    )
    PackerTemplate.objects.filter(installer_config_id=config.pk).update(build_status="pending")


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0021_packerpluginsettings_fileserver_package_credentials"),
    ]

    operations = [
        migrations.RunPython(update_fileserver_package_settings_comment, migrations.RunPython.noop),
    ]
