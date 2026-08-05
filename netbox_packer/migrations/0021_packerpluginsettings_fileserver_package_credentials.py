from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0020_seed_influxdb_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="packerpluginsettings",
            name="fileserver_package_read_user",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="packerpluginsettings",
            name="fileserver_package_read_token_encrypted",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text=(
                    "Fernet-encrypted package-index read token "
                    "(set via set_fileserver_package_read_token())."
                ),
                max_length=512,
            ),
        ),
    ]
