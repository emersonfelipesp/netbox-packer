"""Add monitoring-agent injection fields to PackerTemplate."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_packer", "0007_seed_influxdb_cloud_init"),
    ]

    operations = [
        migrations.AddField(
            model_name="packertemplate",
            name="install_qemu_guest_agent",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Inject qemu-guest-agent package + systemctl enable into the cloud-config at build time. "
                    "Skipped if the installer config already contains qemu-guest-agent."
                ),
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="install_zabbix_agent2",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Inject Zabbix Agent 2 bootstrap into the cloud-config at build time. "
                    "Skipped entirely if the installer config already mentions zabbix-agent2."
                ),
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="zabbix_server",
            field=models.CharField(
                blank=True,
                default="zabbix.nmulti.cloud",
                help_text="Zabbix server address for the injected agent config (ServerActive= directive).",
                max_length=255,
            ),
        ),
    ]
