from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0022_update_fileserver_package_settings_comment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="packerpluginsettings",
            name="proxbox_api_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text=(
                    "Base URL of the proxbox-api backend used to bake cloud-init template images "
                    "(HTTPS required except for literal loopback development endpoints). Required "
                    "for cloud_config installer-config builds."
                ),
            ),
        ),
    ]
