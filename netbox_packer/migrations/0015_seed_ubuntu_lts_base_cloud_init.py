import hashlib

from django.db import migrations

# Minimal base cloud-config shared by the three Ubuntu LTS templates. The QEMU
# Guest Agent, Zabbix Agent 2, and ssh_pwauth are injected at build time by
# netbox_packer.jobs._inject_monitoring_agents (respecting the template's
# install_qemu_guest_agent / install_zabbix_agent2 flags). Per-VM username,
# password (Proxmox cipassword), and SSH public keys are supplied at clone time
# by Proxmox cloud-init; nothing sensitive is baked into the golden image.
UBUNTU_LTS_BASE_CLOUD_CONFIG = """#cloud-config
# N-MultiCloud base Ubuntu LTS cloud-init golden template.
# Template bake identity marker: ubuntu-lts-cloudinit-base.
package_update: true
package_upgrade: false
"""

CONFIG_NAME = "ubuntu-lts-base-cloud-config"
CONFIG_VERSION = "1.0.0"

# Real bakes target the CLUSTER01-DC01 PVE cluster host 10.0.30.71 by default.
# Operators may override node and VMID at build dispatch when needed.
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"

# (template name, os_version, VMID) — the past three Ubuntu LTS releases.
UBUNTU_LTS_TEMPLATES = [
    ("ubuntu-2204-cloudinit-base", "22.04", 9040),
    ("ubuntu-2404-cloudinit-base", "24.04", 9041),
    ("ubuntu-2604-cloudinit-base", "26.04", 9042),
]


def seed_ubuntu_lts_base(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config, _ = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults={
            "os_family": "ubuntu",
            "installer_type": "cloud_config",
            "content": UBUNTU_LTS_BASE_CLOUD_CONFIG,
            "checksum": hashlib.sha256(UBUNTU_LTS_BASE_CLOUD_CONFIG.encode()).hexdigest(),
            "description": (
                "Base Ubuntu LTS cloud-config for the customer VM catalog. The "
                "QEMU Guest Agent, Zabbix Agent 2 (zabbix.nmulti.cloud), and "
                "ssh_pwauth are injected at build time; username/password/SSH "
                "keys are provided per-clone by Proxmox cloud-init."
            ),
        },
    )

    for template_name, os_version, vmid in UBUNTU_LTS_TEMPLATES:
        PackerTemplate.objects.get_or_create(
            name=template_name,
            defaults={
                "os_family": "ubuntu",
                "os_version": os_version,
                "proxmox_template_id": vmid,
                "proxmox_endpoint": PROXMOX_ENDPOINT,
                "proxmox_node": PROXMOX_NODE,
                "storage_pool": "local",
                "cloud_init_ready": True,
                "build_status": "pending",
                "packer_template_ref": "",
                "installer_config": config,
                "install_qemu_guest_agent": True,
                "install_zabbix_agent2": True,
                "zabbix_server": "zabbix.nmulti.cloud",
                "description": (
                    f"Base Ubuntu {os_version} LTS cloud-init template (VMID "
                    f"{vmid}). Bakes via proxbox-api on CLUSTER01-DC01 host "
                    "10.0.30.71 using storage 'local'. Ships qemu-guest-agent, "
                    "zabbix-agent2 -> zabbix.nmulti.cloud, and password SSH "
                    "(ssh_pwauth); key-based SSH stays the default."
                ),
            },
        )


def unseed_ubuntu_lts_base(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    PackerTemplate.objects.filter(
        name__in=[name for name, _os, _vmid in UBUNTU_LTS_TEMPLATES]
    ).delete()
    PackerInstallerConfig.objects.filter(name=CONFIG_NAME, version=CONFIG_VERSION).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0014_seed_fileserver_allinone_cloud_init"),
    ]

    operations = [
        migrations.RunPython(seed_ubuntu_lts_base, unseed_ubuntu_lts_base),
    ]
