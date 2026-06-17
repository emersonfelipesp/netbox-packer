packer {
  required_plugins {
    proxmox = {
      source  = "github.com/hashicorp/proxmox"
      version = ">= 1.2.3"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables — all have usable defaults; override via -var or PackerBuild.variable_overrides
# ---------------------------------------------------------------------------

variable "proxmox_url" {
  type        = string
  default     = ""
  description = "Proxmox API URL (e.g. https://10.0.30.71:8006/api2/json). Falls back to PROXMOX_URL env var."
}

variable "proxmox_node" {
  type        = string
  default     = "10.0.30.71"
  description = "Proxmox node name/IP to build on."
}

variable "proxmox_storage_pool" {
  type        = string
  default     = "local"
  description = "Storage pool for VM disks, EFI, and TPM state."
}

variable "vm_id" {
  type        = number
  default     = 9019
  description = "Proxmox VMID for the template (must not collide with existing VMs)."
}

variable "vm_name" {
  type        = string
  default     = "windows-11-24h2-build"
  description = "Temporary VM name during the build."
}

variable "template_name" {
  type        = string
  default     = "windows-11-24h2-cloudbase"
  description = "Name of the resulting Proxmox template."
}

variable "windows_iso" {
  type        = string
  default     = "local:iso/Win11_24H2_EnglishInternational_x64.iso"
  description = "Proxmox storage ref for the Windows 11 ISO."
}

variable "windows_iso_checksum" {
  type        = string
  default     = "none"
  description = "SHA256 checksum for the Windows 11 ISO, or 'none' to skip."
}

variable "virtio_iso" {
  type        = string
  default     = "local:iso/virtio-win.iso"
  description = "Proxmox storage ref for the virtio-win drivers ISO."
}

variable "memory" {
  type        = number
  default     = 4096
  description = "RAM for the build VM in MiB (Windows 11 minimum is 4096)."
}

variable "cores" {
  type        = number
  default     = 2
  description = "vCPU cores for the build VM."
}

variable "cpu_type" {
  type        = string
  default     = "host"
  description = "CPU type for the build VM."
}

variable "disk_size" {
  type        = string
  default     = "60G"
  description = "OS disk size for the Windows 11 template."
}

variable "network_bridge" {
  type        = string
  default     = "vmbr0"
  description = "Proxmox network bridge to attach the build VM to."
}

variable "winrm_username" {
  type        = string
  default     = "packer"
  description = "Local Windows user created by autounattend.xml for WinRM communicator."
}

variable "winrm_password" {
  # Supply at build time via PKR_VAR_winrm_password env var (or -var).
  # This value is injected into autounattend.xml via templatefile() at build time
  # and is never stored in source control.
  type        = string
  default     = ""
  sensitive   = true
  description = "Build-time WinRM password. Set PKR_VAR_winrm_password in the RQ worker environment."
}

# ---------------------------------------------------------------------------
# Source: proxmox-iso builder for Windows 11
# ---------------------------------------------------------------------------

source "proxmox-iso" "windows11" {
  # --- Proxmox connection ---
  proxmox_url              = env("PROXMOX_URL") != "" ? env("PROXMOX_URL") : var.proxmox_url
  username                 = env("PROXMOX_USERNAME")
  token                    = env("PROXMOX_TOKEN")
  insecure_skip_tls_verify = true

  # --- VM placement ---
  node          = var.proxmox_node
  vm_id         = var.vm_id
  vm_name       = var.vm_name
  template_name = var.template_name

  # --- Firmware: UEFI + OVMF (required for Windows 11) ---
  bios    = "ovmf"
  machine = "q35"

  efi_config {
    efi_storage_pool  = var.proxmox_storage_pool
    efi_type          = "4m"
    pre_enrolled_keys = true
  }

  # TPM 2.0 emulation — satisfies the Windows 11 hardware requirement
  tpm_config {
    tpm_storage_pool = var.proxmox_storage_pool
    tpm_version      = "v2.0"
  }

  # --- Compute ---
  memory   = var.memory
  cores    = var.cores
  cpu_type = var.cpu_type

  # --- Windows 11 installation ISO ---
  iso_file     = var.windows_iso
  iso_checksum = var.windows_iso_checksum

  # --- Additional ISOs ---
  additional_iso_files {
    # virtio-win drivers: Windows Setup reads drivers from all CD-ROMs.
    # autounattend.xml lists E:\ and F:\ as candidate DriverPaths so the correct
    # drive is found regardless of assignment order.
    device       = "ide1"
    iso_file     = var.virtio_iso
    iso_checksum = "none"
    unmount      = true
  }

  additional_iso_files {
    # Autounattend CD: Packer generates a minimal ISO from cd_content and
    # attaches it as a CD-ROM. Windows Setup auto-detects autounattend.xml on
    # any attached drive at the start of the windowsPE phase.
    # templatefile() injects var.winrm_password at build time; the password
    # is never written to any file in the repository.
    device   = "ide2"
    unmount  = true
    cd_label = "UNATTEND"
    cd_content = {
      "autounattend.xml" = templatefile(
        "${path.root}/windows-11/autounattend.xml.tpl",
        { winrm_password = var.winrm_password }
      )
    }
  }

  # --- OS disk ---
  disks {
    disk_size    = var.disk_size
    storage_pool = var.proxmox_storage_pool
    type         = "virtio"
    format       = "raw"
    discard      = true
    io_thread    = true
  }

  # --- Network ---
  network_adapters {
    model  = "virtio"
    bridge = var.network_bridge
  }

  # --- QEMU guest agent (installed via virtio-win-gt-x64.msi) ---
  qemu_agent = true

  # --- WinRM communicator ---
  communicator   = "winrm"
  winrm_username = var.winrm_username
  winrm_password = var.winrm_password
  winrm_port     = 5985
  winrm_use_ssl  = false
  winrm_timeout  = "2h"

  # UEFI auto-boots from DVD; no explicit boot_command needed
  boot_wait = "3s"
}

# ---------------------------------------------------------------------------
# Build: provisioning steps run over WinRM after Windows Setup completes
# ---------------------------------------------------------------------------

build {
  sources = ["source.proxmox-iso.windows11"]

  # 1. Install virtio guest tools (network, storage, balloon, serial, QEMU agent)
  provisioner "powershell" {
    inline = [
      "Write-Host 'Looking for virtio-win-gt-x64.msi on attached CD-ROMs...'",
      "$virtio = (Get-Volume | Where-Object { $_.DriveType -eq 'CD-ROM' } | Where-Object { Test-Path \"$($_.DriveLetter):\\virtio-win-gt-x64.msi\" } | Select-Object -First 1).DriveLetter",
      "if ($virtio) {",
      "  $msi = \"$($virtio):\\virtio-win-gt-x64.msi\"",
      "  Write-Host \"Installing $msi ...\"",
      "  Start-Process msiexec -ArgumentList '/i', $msi, '/qn', '/norestart',",
      "    'ADDLOCAL=FE_balloon_driver,FE_netkvm_driver,FE_pvpanic_driver,FE_qemufwcfg_driver,FE_qemupciserial_driver,FE_vioinput_driver,FE_vioscsi_driver,FE_vioserial_driver,FE_viostor_driver'",
      "    -Wait",
      "  Write-Host 'virtio-win-gt-x64.msi installed.'",
      "} else {",
      "  Write-Warning 'virtio-win ISO not found on any CD-ROM; skipping guest tools install.'",
      "}",
    ]
  }

  # 2. Install cloudbase-init (Windows cloud-init equivalent for Proxmox cloud-init)
  provisioner "powershell" {
    script = "${path.root}/windows-11/scripts/install-cloudbase-init.ps1"
  }

  # 3. Sysprep — generalize the image so each clone gets a unique SID + hostname
  provisioner "powershell" {
    script = "${path.root}/windows-11/scripts/sysprep.ps1"
  }
}
