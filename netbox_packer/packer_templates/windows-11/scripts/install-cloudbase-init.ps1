#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install cloudbase-init on Windows 11 during a Packer build.

.DESCRIPTION
    Downloads and silently installs the official cloudbase-init MSI from the
    CloudBase Solutions distribution endpoint, then writes a minimal
    cloudbase-init.conf pointing at the Proxmox NoCloud/ConfigDrive metadata
    endpoint.

    cloudbase-init is the Windows equivalent of cloud-init and is required for
    Proxmox's "Cloud Init" template feature to:
      - Set the hostname from the VM name
      - Inject SSH public keys into C:\Users\<user>\.ssh\authorized_keys
      - Run vendor_data / user_data scripts on first boot
      - Configure network interfaces (IP, DNS, gateway) via Proxmox NoCloud drive

    This script is executed by Packer over WinRM after the Windows installation
    completes.  Sysprep (sysprep.ps1) must run AFTER this script so that
    cloudbase-init's Sysprep integration seals the image correctly.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallerUrl = "https://cloudbase.it/downloads/CloudbaseInitSetup_x64.msi"
$InstallerPath = "$env:TEMP\CloudbaseInitSetup_x64.msi"
$LogPath = "$env:TEMP\cloudbase-init-install.log"

Write-Host "[cloudbase-init] Downloading installer from $InstallerUrl ..."

# Use TLS 1.2+ for the download (Windows 11 default; explicit for compatibility)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$client = New-Object System.Net.WebClient
try {
    $client.DownloadFile($InstallerUrl, $InstallerPath)
} finally {
    $client.Dispose()
}

Write-Host "[cloudbase-init] Installing (silent, no reboot) ..."

$msiArgs = @(
    "/i", $InstallerPath,
    "/qn",
    "/l*v", $LogPath,
    "/norestart",
    "USERNAME=Administrator",
    "LOGGINGSERIALPORTNAME=COM1"
)

$proc = Start-Process -FilePath msiexec.exe -ArgumentList $msiArgs -PassThru -Wait

if ($proc.ExitCode -notin @(0, 3010)) {
    throw "[cloudbase-init] msiexec exited with code $($proc.ExitCode). See $LogPath for details."
}

Write-Host "[cloudbase-init] Installer exit code: $($proc.ExitCode)"

# ---------------------------------------------------------------------------
# Write cloudbase-init.conf
# ---------------------------------------------------------------------------

$ConfDir  = "C:\Program Files\Cloudbase Solutions\Cloudbase-Init\conf"
$ConfFile = Join-Path $ConfDir "cloudbase-init.conf"

Write-Host "[cloudbase-init] Writing $ConfFile ..."

$ConfContent = @"
[DEFAULT]
username=Administrator
groups=Administrators
inject_user_password=true
config_drive_raw_hhd=true
config_drive_cdrom=true
config_drive_vfat=true
bsdtar_path=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\bin\bsdtar.exe
mtools_path=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\bin\

verbose=true
debug=true
logdir=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\log\
logfile=cloudbase-init.log
default_log_levels=comtypes=INFO,suds=INFO,iso8601=WARN,requests=WARN

logging_serial_port_settings=COM1,115200,N,8

mtu_use_dhcp_config=true
ntp_use_dhcp_config=true

local_scripts_path=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\LocalScripts\

metadata_services=cloudbaseinit.metadata.services.configdrive.ConfigDriveService,
    cloudbaseinit.metadata.services.httpservice.HttpService,
    cloudbaseinit.metadata.services.ec2metadataservice.EC2MetadataService

plugins=cloudbaseinit.plugins.common.mtu.MTUPlugin,
    cloudbaseinit.plugins.windows.ntpclient.NTPClientPlugin,
    cloudbaseinit.plugins.common.sshpublickeys.SetUserSSHPublicKeysPlugin,
    cloudbaseinit.plugins.common.setuserpassword.SetUserPasswordPlugin,
    cloudbaseinit.plugins.windows.createuser.CreateUserPlugin,
    cloudbaseinit.plugins.common.localscripts.LocalScriptsPlugin,
    cloudbaseinit.plugins.common.userdata.UserDataPlugin,
    cloudbaseinit.plugins.windows.extendvolumes.ExtendVolumesPlugin,
    cloudbaseinit.plugins.windows.winrmlistener.ConfigWinRMListenerPlugin,
    cloudbaseinit.plugins.windows.winrmcertificateauth.ConfigWinRMCertificateAuthPlugin

allow_reboot=true
stop_service_on_exit=false
check_latest_version=false
"@

# Write with UTF-8 encoding (no BOM) — cloudbase-init requires this
[System.IO.File]::WriteAllText($ConfFile, $ConfContent, [System.Text.Encoding]::UTF8)

Write-Host "[cloudbase-init] Configuration written."

# ---------------------------------------------------------------------------
# Also write the Unattend post-specialize conf (used during sysprep)
# ---------------------------------------------------------------------------

$UnattendConfFile = Join-Path $ConfDir "cloudbase-init-unattend.conf"

$UnattendConfContent = @"
[DEFAULT]
username=Administrator
groups=Administrators
inject_user_password=true
first_logon_behaviour=no

metadata_services=cloudbaseinit.metadata.services.configdrive.ConfigDriveService

plugins=cloudbaseinit.plugins.common.mtu.MTUPlugin,
    cloudbaseinit.plugins.windows.extendvolumes.ExtendVolumesPlugin,
    cloudbaseinit.plugins.common.userdata.UserDataPlugin

allow_reboot=true
stop_service_on_exit=false
check_latest_version=false
"@

[System.IO.File]::WriteAllText($UnattendConfFile, $UnattendConfContent, [System.Text.Encoding]::UTF8)

Write-Host "[cloudbase-init] Unattend configuration written."
Write-Host "[cloudbase-init] Installation complete."
