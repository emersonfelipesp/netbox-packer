from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netbox_packer", "0034_seed_network_appliance_marker")]

    operations = [
        migrations.AlterField(
            model_name="packerinstallerconfig",
            name="installer_type",
            field=models.CharField(
                choices=[
                    ("autoinstall", "Cloud-init autoinstall (Ubuntu)"),
                    ("kickstart", "Anaconda kickstart (RHEL-family)"),
                    ("preseed", "d-i preseed (Debian)"),
                    ("cloud_config", "Cloud-config YAML (#cloud-config, Proxmox/generic)"),
                    ("vendor_data", "Cloud-init vendor-data (keeps per-VM Cloud-Init fields)"),
                    ("unattend", "Windows Unattend XML (autounattend.xml)"),
                ],
                max_length=20,
            ),
        ),
    ]
