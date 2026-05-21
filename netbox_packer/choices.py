from utilities.choices import ChoiceSet


class OSFamilyChoices(ChoiceSet):
    key = "PackerTemplate.os_family"

    CHOICE_UBUNTU = "ubuntu"
    CHOICE_RHEL = "rhel"
    CHOICE_DEBIAN = "debian"
    CHOICE_PROXMOX_VE = "proxmox_ve"
    CHOICE_PROXMOX_BS = "proxmox_bs"
    CHOICE_PROXMOX_PDM = "proxmox_pdm"

    CHOICES = [
        (CHOICE_UBUNTU, "Ubuntu", "blue"),
        (CHOICE_RHEL, "RHEL-family", "red"),
        (CHOICE_DEBIAN, "Debian", "green"),
        (CHOICE_PROXMOX_VE, "Proxmox VE", "purple"),
        (CHOICE_PROXMOX_BS, "Proxmox Backup Server", "orange"),
        (CHOICE_PROXMOX_PDM, "Proxmox Datacenter Manager", "cyan"),
    ]


class StoragePoolTypeChoices(ChoiceSet):
    key = "PackerTemplate.storage_pool_type"

    CHOICE_LVMTHIN = "lvmthin"
    CHOICE_ZFSPOOL = "zfspool"
    CHOICE_DIR = "dir"

    CHOICES = [
        (CHOICE_LVMTHIN, "LVM-Thin", "blue"),
        (CHOICE_ZFSPOOL, "ZFS", "cyan"),
        (CHOICE_DIR, "Directory", "gray"),
    ]


class StorageFormatChoices(ChoiceSet):
    key = "PackerTemplate.storage_format"

    CHOICE_QCOW2 = "qcow2"
    CHOICE_RAW = "raw"
    CHOICE_VMDK = "vmdk"

    CHOICES = [
        (CHOICE_QCOW2, "qcow2", "blue"),
        (CHOICE_RAW, "raw", "gray"),
        (CHOICE_VMDK, "VMDK", "orange"),
    ]


class BuildStatusChoices(ChoiceSet):
    key = "PackerTemplate.build_status"

    CHOICE_PENDING = "pending"
    CHOICE_BUILDING = "building"
    CHOICE_READY = "ready"
    CHOICE_FAILED = "failed"
    CHOICE_DEPRECATED = "deprecated"
    CHOICE_STALE = "stale"

    CHOICES = [
        (CHOICE_PENDING, "Pending", "gray"),
        (CHOICE_BUILDING, "Building", "blue"),
        (CHOICE_READY, "Ready", "green"),
        (CHOICE_FAILED, "Failed", "red"),
        (CHOICE_DEPRECATED, "Deprecated", "orange"),
        (CHOICE_STALE, "Stale", "yellow"),
    ]
