import hashlib

from django.db import migrations

# ---------------------------------------------------------------------------
# Windows 11 unattend XML template content descriptor
# The actual autounattend.xml is generated at build time by Packer's
# templatefile() function from:
#   netbox_packer/packer_templates/windows-11/autounattend.xml.tpl
# The content here is a descriptive record.  Packer reads the .tpl directly.
# ---------------------------------------------------------------------------

WIN11_UNATTEND_CONTENT = """\
# Windows 11 24H2 unattended setup configuration (Packer HCL2 path)
#
# Build toolchain:
#   Template : netbox_packer/packer_templates/windows-11-proxmox.pkr.hcl
#   Unattend : netbox_packer/packer_templates/windows-11/autounattend.xml.tpl
#   Scripts  : netbox_packer/packer_templates/windows-11/scripts/
#
# The autounattend.xml is injected at build time via Packer's cd_content +
# templatefile() mechanism.  The WinRM password is sourced from
# PKR_VAR_winrm_password environment variable on the netbox-rq worker.
# No password is stored in this record.
#
# Passes configured:
#   windowsPE  - VirtIO storage driver injection, Win11 hardware bypass,
#                UEFI/GPT disk layout (EFI 100 MB + MSR 16 MB + OS rest)
#   specialize - WinRM HTTP enable (port 5985), computer name WIN-PACKER-BUILD
#   oobeSystem - OOBE skip, build-time local admin, auto-logon for WinRM
#
# Post-install provisioners (via WinRM):
#   1. VirtIO guest tools install  (inline PowerShell in .pkr.hcl)
#   2. cloudbase-init install      (install-cloudbase-init.ps1)
#   3. sysprep generalize          (sysprep.ps1)
#
# ISO prerequisites (must be uploaded to local storage on 10.0.30.71):
#   - local:iso/Win11_24H2_EnglishInternational_x64.iso
#   - local:iso/virtio-win.iso
#
# Required RQ worker environment variables:
#   PROXMOX_URL        = https://10.0.30.71:8006/api2/json
#   PROXMOX_USERNAME   = root@pam
#   PROXMOX_TOKEN      = <api-token-id>=<api-token-secret>
#   PKR_VAR_winrm_password = <build-time-password> (sensitive, not stored)
"""

WIN11_CONFIG_NAME = "windows-11-24h2"
WIN11_CONFIG_VERSION = "1.0.0"
WIN11_TEMPLATE_NAME = "windows-11-24h2"
WIN11_VMID = 9019

PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"

PACKER_HCL_REF = (
    "/opt/netbox/netbox/netbox-packer/netbox_packer/"
    "packer_templates/windows-11-proxmox.pkr.hcl"
)


def seed_windows11(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    win11_config, _ = PackerInstallerConfig.objects.get_or_create(
        name=WIN11_CONFIG_NAME,
        version=WIN11_CONFIG_VERSION,
        defaults={
            "os_family": "windows",
            "installer_type": "unattend",
            "content": WIN11_UNATTEND_CONTENT,
            "checksum": hashlib.sha256(WIN11_UNATTEND_CONTENT.encode()).hexdigest(),
            "description": (
                "Windows 11 24H2 unattended setup via Packer HCL2 proxmox-iso builder. "
                "autounattend.xml is generated at build time from a Packer templatefile() "
                "template; WinRM password is supplied via PKR_VAR_winrm_password env var "
                "on the netbox-rq worker (never stored). Post-install: VirtIO guest tools, "
                "cloudbase-init (Proxmox cloud-init support), sysprep generalize. "
                "Targets Proxmox 10.0.30.71, VMID 9019, storage local."
            ),
        },
    )

    PackerTemplate.objects.get_or_create(
        name=WIN11_TEMPLATE_NAME,
        defaults={
            "os_family": "windows",
            "os_version": "24H2",
            "proxmox_template_id": WIN11_VMID,
            "proxmox_endpoint": PROXMOX_ENDPOINT,
            "proxmox_node": PROXMOX_NODE,
            "storage_pool": "local",
            "cloud_init_ready": True,
            "build_status": "pending",
            "packer_template_ref": PACKER_HCL_REF,
            "installer_config": win11_config,
            # Monitoring agents are installed by the cloudbase-init + sysprep
            # provisioner scripts in the HCL; these flags are kept True so the
            # template record accurately reflects what is installed in the image.
            "install_qemu_guest_agent": True,
            "install_zabbix_agent2": True,
            "zabbix_server": "zabbix.nmulti.cloud",
            "description": (
                "Windows 11 24H2 Proxmox cloud-init template (VMID 9019). "
                "Built via Packer HCL2 proxmox-iso builder on ProxmoxEndpoint "
                "10.0.30.71 (storage: local). Includes: VirtIO drivers, "
                "cloudbase-init for Proxmox cloud-init support (hostname, SSH keys, "
                "user-data, network), QEMU guest agent. Generalized with sysprep; "
                "each VM clone receives a unique SID and hostname on first boot. "
                "Requires Packer ≥ 1.11.0 and packer-plugin-proxmox ≥ 1.2.3."
            ),
        },
    )


def unseed_windows11(apps, schema_editor):
    # Intentionally a no-op: do not delete operator data on reverse migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0012_seed_powerdns_cloud_init"),
    ]

    operations = [
        migrations.RunPython(seed_windows11, unseed_windows11),
    ]
