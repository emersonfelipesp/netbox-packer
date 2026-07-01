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


# OS versions offered per OS family on the Packer template form. The stored
# value is the bare version string (e.g. "24.04"); the OS family provides the
# disambiguating context. Extend a family's list to offer a new version in the
# dropdown — no migration is required because the model field stays free-form
# (the REST API therefore keeps accepting arbitrary versions for automation).
OS_VERSIONS_BY_FAMILY = {
    OSFamilyChoices.CHOICE_UBUNTU: [
        ("26.04", "Ubuntu 26.04 LTS"),
        ("24.04", "Ubuntu 24.04 LTS (Noble)"),
        ("22.04", "Ubuntu 22.04 LTS (Jammy)"),
        ("20.04", "Ubuntu 20.04 LTS (Focal)"),
    ],
    OSFamilyChoices.CHOICE_DEBIAN: [
        ("13", "Debian 13 (Trixie)"),
        ("12", "Debian 12 (Bookworm)"),
        ("11", "Debian 11 (Bullseye)"),
    ],
    OSFamilyChoices.CHOICE_RHEL: [
        ("10", "RHEL-family 10"),
        ("9", "RHEL-family 9"),
        ("8", "RHEL-family 8"),
    ],
    OSFamilyChoices.CHOICE_PROXMOX_VE: [
        ("9", "Proxmox VE 9"),
        ("8", "Proxmox VE 8"),
    ],
    OSFamilyChoices.CHOICE_PROXMOX_BS: [
        ("4", "Proxmox Backup Server 4"),
        ("3", "Proxmox Backup Server 3"),
    ],
    OSFamilyChoices.CHOICE_PROXMOX_PDM: [
        ("1.0", "Proxmox Datacenter Manager 1.0"),
        ("0.9", "Proxmox Datacenter Manager 0.9 (beta)"),
    ],
}

# Human-readable group label keyed by OS family value, used for the optgroups
# rendered in the OS version dropdown.
_OS_FAMILY_LABELS = {value: label for value, label, *_ in OSFamilyChoices.CHOICES}


def os_version_grouped_choices():
    """Return Django optgroup-style choices for os_version, grouped by OS family.

    Shape: ``[(family_label, [(version, version_label), ...]), ...]``. This works
    with a plain ``forms.Select`` (no JavaScript required); the progressive
    ``os_version_filter.js`` enhancement narrows the visible options to the
    selected family.
    """
    groups = []
    for family_value, versions in OS_VERSIONS_BY_FAMILY.items():
        group_label = _OS_FAMILY_LABELS.get(family_value, family_value)
        groups.append((group_label, list(versions)))
    return groups


def os_version_known_values():
    """Return the set of every os_version value offered across all families."""
    return {version for versions in OS_VERSIONS_BY_FAMILY.values() for version, _label in versions}


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
