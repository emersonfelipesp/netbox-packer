"""Migration: adds proxbox_api_url and proxbox_api_key to PackerPluginSettings."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0003_packer_plugin_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="packerpluginsettings",
            name="proxbox_api_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Base URL of the proxbox-api instance (e.g. http://10.0.30.207:8000)",
            ),
        ),
        migrations.AddField(
            model_name="packerpluginsettings",
            name="proxbox_api_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="X-Proxbox-API-Key header value for proxbox-api authentication",
                max_length=256,
            ),
        ),
    ]
