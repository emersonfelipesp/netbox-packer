import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0022_update_fileserver_package_settings_comment"),
    ]

    operations = [
        migrations.AddField(
            model_name="packertemplate",
            name="install_nms_agent",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Inject the pinned nms-agent bootstrap into the cloud-config at build time. "
                    "Disabled by default so existing templates are unchanged."
                ),
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="nms_agent_backend_url",
            field=models.URLField(
                default="https://backend.nms.nmulti.cloud",
                validators=[
                    django.core.validators.URLValidator(
                        message="Enter an HTTPS URL for the NMS agent backend.",
                        schemes=["https"],
                    )
                ],
                help_text=("HTTPS NMS backend used by the injected agent for bootstrap, heartbeats, and telemetry."),
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="provisions_service",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                max_length=64,
            ),
        ),
    ]
