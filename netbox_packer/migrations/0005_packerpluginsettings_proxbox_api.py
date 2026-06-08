from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0004_packerpluginsettings_tags_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="packerpluginsettings",
            name="proxbox_api_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text=(
                    "Base URL of the proxbox-api backend used to bake cloud-init template "
                    "images (e.g. http://10.0.30.207:8000). Required for cloud_config "
                    "installer-config builds."
                ),
            ),
        ),
        migrations.AddField(
            model_name="packerpluginsettings",
            name="proxbox_api_key_encrypted",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Fernet-encrypted X-Proxbox-API-Key (set via set_proxbox_api_key()).",
                max_length=512,
            ),
        ),
    ]
