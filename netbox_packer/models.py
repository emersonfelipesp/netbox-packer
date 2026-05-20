import hashlib

from django.db import models
from netbox.models import NetBoxModel

from .choices import (
    BuildStatusChoices,
    OSFamilyChoices,
    StorageFormatChoices,
    StoragePoolTypeChoices,
)

__all__ = (
    "PackerTemplate",
    "PackerInstallerConfig",
    "PackerBuild",
    "PackerBuildTarget",
    "PackerPluginSettings",
)


class PackerInstallerConfig(NetBoxModel):
    """An OS-installer configuration file (autoinstall, kickstart, preseed)."""

    INSTALLER_TYPE_CHOICES = [
        ("autoinstall", "Cloud-init autoinstall (Ubuntu)"),
        ("kickstart", "Anaconda kickstart (RHEL-family)"),
        ("preseed", "d-i preseed (Debian)"),
    ]

    name = models.CharField(max_length=100)
    os_family = models.CharField(
        max_length=20,
        choices=OSFamilyChoices,
    )
    installer_type = models.CharField(
        max_length=20,
        choices=INSTALLER_TYPE_CHOICES,
    )
    content = models.TextField()
    version = models.CharField(max_length=40, default="1.0.0")
    checksum = models.CharField(max_length=64, blank=True, editable=False)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name", "version"]
        verbose_name = "Packer Installer Config"
        verbose_name_plural = "Packer Installer Configs"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"],
                name="netbox_packer_packerinstallerconfig_name_version_uniq",
            )
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("plugins:netbox_packer:packerinstallerconfig", args=[self.pk])

    def save(self, *args, **kwargs):
        self.checksum = hashlib.sha256(self.content.encode()).hexdigest()
        super().save(*args, **kwargs)


class PackerTemplate(NetBoxModel):
    """A Packer-managed Proxmox VM template with lifecycle tracking."""

    name = models.CharField(max_length=100)
    os_family = models.CharField(
        max_length=20,
        choices=OSFamilyChoices,
    )
    os_version = models.CharField(max_length=40)
    proxmox_template_id = models.PositiveIntegerField()
    proxmox_endpoint = models.URLField(blank=True)
    proxmox_node = models.CharField(max_length=100)
    storage_pool = models.CharField(max_length=100, blank=True)
    storage_pool_type = models.CharField(
        max_length=20,
        choices=StoragePoolTypeChoices,
        blank=True,
    )
    storage_format = models.CharField(
        max_length=10,
        choices=StorageFormatChoices,
        blank=True,
    )
    cloud_init_ready = models.BooleanField(default=True)
    min_cpu_type = models.CharField(max_length=40, blank=True)
    build_status = models.CharField(
        max_length=20,
        choices=BuildStatusChoices,
        default=BuildStatusChoices.CHOICE_PENDING,
    )
    built_at = models.DateTimeField(null=True, blank=True)
    packer_template_ref = models.CharField(max_length=255, blank=True)
    max_age_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Rebuild template after this many days",
    )
    auto_rebuild = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    # HCP Packer fields
    hcp_bucket_name = models.CharField(max_length=255, blank=True)
    hcp_channel_name = models.CharField(max_length=255, blank=True)
    hcp_iteration_id = models.CharField(max_length=255, blank=True)
    hcp_build_id = models.CharField(max_length=255, blank=True)
    hcp_last_synced_at = models.DateTimeField(null=True, blank=True)

    # Installer config fields
    installer_config = models.ForeignKey(
        "PackerInstallerConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="templates",
    )
    installer_config_checksum_at_build = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Packer Template"
        verbose_name_plural = "Packer Templates"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("plugins:netbox_packer:packertemplate", args=[self.pk])

    @property
    def age_days(self):
        if self.built_at is None:
            return None
        from django.utils import timezone

        return (timezone.now() - self.built_at).days

    @property
    def is_stale(self):
        if self.max_age_days is None:
            return False
        age = self.age_days
        if age is None:
            return False
        config_stale = (
            self.installer_config is not None
            and self.installer_config_checksum_at_build
            and self.installer_config.checksum != self.installer_config_checksum_at_build
        )
        return age > self.max_age_days or config_stale

    @property
    def derived_vms(self):
        """Return VirtualMachines whose source_packer_template custom field matches this pk."""
        try:
            from virtualization.models import VirtualMachine
        except ImportError:
            return []
        return VirtualMachine.objects.filter(custom_field_data__source_packer_template=self.pk)


class PackerBuild(NetBoxModel):
    """A single build run for a PackerTemplate."""

    BUILD_STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    template = models.ForeignKey(
        PackerTemplate,
        on_delete=models.CASCADE,
        related_name="builds",
    )
    triggered_by = models.CharField(max_length=100, blank=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=BUILD_STATUS_CHOICES,
        default="queued",
    )
    variable_overrides = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    result_template_id = models.IntegerField(null=True, blank=True)
    selected_node = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-queued_at"]
        verbose_name = "Packer Build"
        verbose_name_plural = "Packer Builds"

    def __str__(self):
        return f"Build #{self.pk} for {self.template.name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("plugins:netbox_packer:packerbuild", args=[self.pk])


class PackerBuildTarget(NetBoxModel):
    """A cluster/node target for multi-cluster template distribution."""

    template = models.ForeignKey(
        PackerTemplate,
        on_delete=models.CASCADE,
        related_name="build_targets",
    )
    proxmox_endpoint = models.URLField(blank=True)
    proxmox_node = models.CharField(max_length=100)
    priority = models.PositiveIntegerField(default=10)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "proxmox_node"]
        verbose_name = "Packer Build Target"
        verbose_name_plural = "Packer Build Targets"
        constraints = [
            models.UniqueConstraint(
                fields=["template", "proxmox_node"],
                name="netbox_packer_packerbuildtarget_template_node_uniq",
            )
        ]

    def __str__(self):
        return f"{self.template.name} -> {self.proxmox_node}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("plugins:netbox_packer:packerbuildtarget", args=[self.pk])


PACKER_BRANCH_ON_CONFLICT_CHOICES = (
    ("fail", "Fail and leave branch open for review"),
    ("acknowledge", "Acknowledge conflicts and merge anyway"),
)


class PackerPluginSettings(NetBoxModel):
    """Singleton-style settings row for netbox-packer branching behavior."""

    singleton_key = models.CharField(
        max_length=32,
        unique=True,
        default="default",
        editable=False,
    )
    branching_enabled = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, PackerStalenessCheckJob creates a netbox-branching branch, "
            "writes stale-status updates against that branch, and merges on success."
        ),
    )
    branch_name_prefix = models.CharField(
        max_length=64,
        default="packer-stale",
    )
    branch_on_conflict = models.CharField(
        max_length=16,
        choices=PACKER_BRANCH_ON_CONFLICT_CHOICES,
        default="fail",
    )

    class Meta:
        verbose_name = "Packer Plugin Settings"
        verbose_name_plural = "Packer Plugin Settings"

    def __str__(self):
        return "Packer plugin settings"

    def save(self, *args, **kwargs):
        self.singleton_key = "default"
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _created = cls.objects.get_or_create(singleton_key="default")
        return obj
