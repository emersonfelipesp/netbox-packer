#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Generalize the Windows 11 template image using sysprep.

.DESCRIPTION
    Runs Windows System Preparation Tool (sysprep) with:
      /oobe      — marks the image so the next boot runs OOBE (Out-of-Box Experience)
      /generalize — removes machine-specific information (SID, installed hardware
                    inventory, event logs) so clones receive unique identities
      /quit      — exits sysprep after generalizing without powering off

    Packer itself shuts the VM down after this provisioner returns.  Shutting
    down inside sysprep (/shutdown) can cause Packer to lose the WinRM session
    before it confirms provisioner success.

    For cloudbase-init integration, sysprep also invokes the cloudbase-init
    Unattend configuration (via cloudbase-init-unattend.conf) to run a minimal
    set of post-specialize plugins on the first boot of each cloned VM.

    ⚠ This script is the last Packer provisioner.  After it exits, Packer
       converts the VM to a Proxmox template.  Do not add provisioners after
       this one.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SysprepExe = "$env:SystemRoot\System32\Sysprep\sysprep.exe"

if (-not (Test-Path $SysprepExe)) {
    throw "sysprep.exe not found at $SysprepExe"
}

# Check if cloudbase-init Unattend conf is present; if so, pass it as the
# Sysprep answer file so cloudbase-init's post-specialize plugins run on
# first boot of each cloned VM.
$CloudbaseUnattendConf = "C:\Program Files\Cloudbase Solutions\Cloudbase-Init\conf\cloudbase-init-unattend.conf"

$SysprepArgs = @("/oobe", "/generalize", "/quit")

if (Test-Path $CloudbaseUnattendConf) {
    Write-Host "[sysprep] cloudbase-init Unattend configuration found."
    # Enable the cloudbase-init Unattend service so it runs on first boot
    $UnattendServiceName = "cloudbase-init-unattend"
    $Svc = Get-Service -Name $UnattendServiceName -ErrorAction SilentlyContinue
    if ($Svc) {
        Set-Service -Name $UnattendServiceName -StartupType Automatic
        Write-Host "[sysprep] Enabled $UnattendServiceName service for first-boot run."
    } else {
        Write-Warning "[sysprep] $UnattendServiceName service not found; skipping service enable."
    }
} else {
    Write-Warning "[sysprep] cloudbase-init Unattend conf not found at $CloudbaseUnattendConf"
    Write-Warning "[sysprep] Sysprep will run without cloudbase-init Unattend integration."
}

Write-Host "[sysprep] Running: $SysprepExe $($SysprepArgs -join ' ')"

$proc = Start-Process -FilePath $SysprepExe -ArgumentList $SysprepArgs -PassThru -Wait

Write-Host "[sysprep] sysprep.exe exited with code $($proc.ExitCode)"

if ($proc.ExitCode -ne 0) {
    # Dump sysprep log to output for Packer build log capture
    $SysprepLog = "$env:SystemRoot\System32\Sysprep\Panther\setuperr.log"
    if (Test-Path $SysprepLog) {
        Write-Warning "[sysprep] Error log content:"
        Get-Content $SysprepLog | Write-Warning
    }
    throw "[sysprep] sysprep.exe exited with non-zero code $($proc.ExitCode)"
}

Write-Host "[sysprep] Generalization complete. Packer will shut down and convert VM to template."
