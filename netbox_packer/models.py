import base64
import hashlib

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db import models
from netbox.models import NetBoxModel

from .base_image import (
    base_image_pin_applies,
    pin_differs_from_built_source,
    validate_base_image_url,
)
from .choices import (
    BuildStatusChoices,
    OSFamilyChoices,
    StorageFormatChoices,
    StoragePoolTypeChoices,
)

NMS_AGENT_BACKEND_URL_VALIDATOR = URLValidator(
    schemes=["https"],
    message="Enter an HTTPS URL for the NMS agent backend.",
)


def _fernet():
    """Build a Fernet cipher from the Django SECRET_KEY (no external key management)."""
    from cryptography.fernet import Fernet
    from django.conf import settings

    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


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
        ("cloud_config", "Cloud-config YAML (#cloud-config, Proxmox/generic)"),
        ("unattend", "Windows Unattend XML (autounattend.xml)"),
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

    name = models.CharField(max_length=100, unique=True)
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

    # Monitoring agent injection (applied at build time by PackerBuildJob)
    install_qemu_guest_agent = models.BooleanField(
        default=True,
        help_text=(
            "Inject qemu-guest-agent package + systemctl enable into the cloud-config at build time. "
            "Skipped if the installer config already contains qemu-guest-agent."
        ),
    )
    install_zabbix_agent2 = models.BooleanField(
        default=True,
        help_text=(
            "Inject Zabbix Agent 2 bootstrap into the cloud-config at build time. "
            "Skipped entirely if the installer config already mentions zabbix-agent2."
        ),
    )
    install_nms_agent = models.BooleanField(
        default=False,
        help_text=(
            "Inject the pinned nms-agent bootstrap into the cloud-config at build time. "
            "Disabled by default so existing templates are unchanged."
        ),
    )
    nms_agent_backend_url = models.URLField(
        default="https://backend.nms.nmulti.cloud",
        validators=[NMS_AGENT_BACKEND_URL_VALIDATOR],
        help_text=("HTTPS NMS backend used by the injected agent for bootstrap, heartbeats, and telemetry."),
    )
    zabbix_server = models.CharField(
        max_length=255,
        default="zabbix.nmulti.cloud",
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z0-9.\-\[\]]+(:[0-9]{1,5})?(,[A-Za-z0-9.\-\[\]]+(:[0-9]{1,5})?)*$",
                message=(
                    "Enter a valid Zabbix server address. Allowed: hostname or IP (optionally with "
                    ":port), multiple entries comma-separated. No spaces or shell metacharacters."
                ),
            )
        ],
        help_text=(
            "Zabbix server address for the injected agent config (ServerActive= directive). "
            "Accepts hostname or IP with optional :port; comma-separate multiple entries. "
            "Only alphanumeric characters, dots, hyphens, brackets, colons, and commas are allowed."
        ),
    )

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

    # Authorization boundary for the File Server package-index credential
    # injection in package_index.py. editable=False keeps it out of
    # PackerTemplateForm (explicit Meta.fields tuple) and out of the DRF
    # serializer's writable fields (also an explicit Meta.fields tuple, and
    # DRF marks a non-editable model field read_only even if listed) — it can
    # only be set by a migration. Renaming the row leaves this True (the
    # trusted template keeps its trust); deleting it and having another row
    # reclaim the freed name leaves this False on the new row (fails closed).
    # `unique=True` on `name` above prevents two rows sharing the name
    # *simultaneously*; this flag is what prevents *reclaiming* the name.
    is_fileserver_golden_template = models.BooleanField(default=False, editable=False)

    # Stable, migration-managed service identity for downstream provisioning
    # hooks. Created VMs already retain source_packer_template, so consumers can
    # follow that lineage to this marker without relying on hostnames or adding
    # a second per-VM tag. Non-editable prevents operators from accidentally
    # changing the meaning of an established golden template.
    provisions_service = models.CharField(max_length=64, blank=True, default="", editable=False)

    # Reproducible, verifiable OS base. Without these the bake resolves the vendor's
    # mutable "latest" directory, so rebuilding the same profile can silently produce a
    # different root filesystem, and the artifact that becomes the guest's entire
    # operating system is accepted with no integrity check.
    #
    # base_image_url pins an exact (normally dated) vendor artifact instead of the
    # derived default. base_image_sha256 is the digest proxbox-api verifies after
    # download. Setting a pinned URL without a digest is refused at build time — see
    # jobs._resolve_cloud_image_source() — because a pin that is not verified only looks
    # like provenance.
    base_image_url = models.URLField(
        blank=True,
        default="",
        max_length=500,
        help_text=(
            "Optional exact base image URL, pinning this template to a specific vendor "
            "artifact instead of the release default. Requires Base image sha256."
        ),
    )
    base_image_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[0-9a-f]{64}$",
                message="Enter a lowercase 64-character hexadecimal sha256 digest.",
            )
        ],
        help_text=(
            "Reviewed sha256 of the base image, verified by proxbox-api after download. "
            "Record where the digest was obtained and verified; an unverified digest "
            "proves nothing."
        ),
    )
    base_image_url_at_build = models.URLField(
        blank=True,
        default="",
        editable=False,
        max_length=500,
        help_text="Resolved base image URL used by the last successful cloud-image build.",
    )
    base_image_sha256_at_build = models.CharField(
        blank=True,
        default="",
        editable=False,
        max_length=64,
        help_text="Resolved base image sha256 used by the last successful cloud-image build.",
    )

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
    def supports_base_image_pin(self):
        """Only the cloud_config builder resolves, verifies, and snapshots a base image."""
        installer = self.installer_config
        return installer is not None and base_image_pin_applies(installer.installer_type)

    def clean(self):
        super().clean()
        if self.base_image_url:
            try:
                self.base_image_url = validate_base_image_url(
                    self.base_image_url,
                    source="base_image_url",
                )
            except ValueError as exc:
                raise ValidationError({"base_image_url": str(exc)}) from exc
        # Reject a pin the builder would silently ignore. Accepting one is worse than
        # useless: it looks like the base image is verified while nothing enforces it, and
        # because the local path records no at-build snapshot the template also becomes
        # permanently stale, which `auto_rebuild` turns into an endless rebuild loop.
        if (self.base_image_url or self.base_image_sha256) and not self.supports_base_image_pin:
            message = (
                "Base image pinning applies only to templates whose installer config is "
                "of type 'cloud_config'. The local Packer builder never resolves a base "
                "image URL, so a pin here would not be enforced and would leave this "
                "template permanently stale."
            )
            field = "base_image_url" if self.base_image_url else "base_image_sha256"
            raise ValidationError({field: message})

    @property
    def is_stale(self):
        if self.built_at is None:
            return False
        config_stale = (
            self.installer_config is not None
            and self.installer_config_checksum_at_build
            and self.installer_config.checksum != self.installer_config_checksum_at_build
        )
        # Base image pins are only meaningful on the cloud_config path: that is the only
        # builder which resolves a vendor image, forwards the digest, and records the
        # at-build snapshot. The local Packer path builds from HCL inputs and writes no
        # snapshot, so a pin there would leave `built_*` permanently empty while
        # `desired_*` is set — reporting the template stale forever and, with
        # `auto_rebuild`, rebuilding it in an endless loop. `clean()` refuses to create
        # that state; this guard also neutralises any row that already carries it.
        base_image_stale = self.supports_base_image_pin and pin_differs_from_built_source(
            desired_url=self.base_image_url,
            desired_sha256=self.base_image_sha256,
            built_url=self.base_image_url_at_build,
            built_sha256=self.base_image_sha256_at_build,
        )
        age = self.age_days
        age_stale = self.max_age_days is not None and age is not None and age > self.max_age_days
        return age_stale or config_stale or base_image_stale

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
    base_image_url_at_build = models.URLField(
        blank=True,
        default="",
        editable=False,
        max_length=500,
        help_text="Resolved base image URL used by this successful cloud-image build.",
    )
    base_image_sha256_at_build = models.CharField(
        blank=True,
        default="",
        editable=False,
        max_length=64,
        help_text="Resolved base image sha256 used by this successful cloud-image build.",
    )

    class Meta:
        ordering = ["-queued_at"]
        verbose_name = "Packer Build"
        verbose_name_plural = "Packer Builds"

    def __str__(self):
        return f"Build #{self.pk} for {self.template.name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("plugins:netbox_packer:packerbuild", args=[self.pk])

    def clean(self):
        super().clean()
        overrides = self.variable_overrides or {}
        if not isinstance(overrides, dict):
            raise ValidationError({"variable_overrides": "variable_overrides must be an object."})
        image_url = overrides.get("image_url")
        if image_url not in (None, ""):
            try:
                validate_base_image_url(
                    image_url,
                    source="variable_overrides['image_url']",
                )
            except ValueError as exc:
                raise ValidationError({"variable_overrides": str(exc)}) from exc


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
    """Singleton-style runtime settings for netbox-packer."""

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
    proxbox_api_url = models.URLField(
        blank=True,
        default="",
        help_text=(
            "Base URL of the proxbox-api backend used to bake cloud-init template images "
            "(e.g. http://10.0.30.207:8000). Required for cloud_config installer-config builds."
        ),
    )
    proxbox_api_key_encrypted = models.CharField(
        max_length=512,
        blank=True,
        default="",
        editable=False,
        help_text="Fernet-encrypted X-Proxbox-API-Key (set via set_proxbox_api_key()).",
    )
    fileserver_package_read_user = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    fileserver_package_read_token_encrypted = models.CharField(
        max_length=512,
        blank=True,
        default="",
        editable=False,
        help_text=("Fernet-encrypted package-index read token (set via set_fileserver_package_read_token())."),
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

    def set_proxbox_api_key(self, plain: str) -> None:
        """Encrypt and store the proxbox-api key (clears it when ``plain`` is empty)."""
        if not plain:
            self.proxbox_api_key_encrypted = ""
            return
        self.proxbox_api_key_encrypted = _fernet().encrypt(plain.encode()).decode()

    def get_proxbox_api_key(self) -> str:
        """Return the decrypted proxbox-api key, or ``""`` when unset/undecryptable."""
        if not self.proxbox_api_key_encrypted:
            return ""
        try:
            return _fernet().decrypt(self.proxbox_api_key_encrypted.encode()).decode()
        except Exception:  # noqa: BLE001 - treat any decrypt failure as "no key"
            return ""

    def set_fileserver_package_read_token(self, plain: str) -> None:
        """Encrypt and store the package-index token (clears it when ``plain`` is empty)."""
        if not plain:
            self.fileserver_package_read_token_encrypted = ""
            return
        self.fileserver_package_read_token_encrypted = _fernet().encrypt(plain.encode()).decode()

    def get_fileserver_package_read_token(self) -> str:
        """Return the decrypted package-index token, or ``""`` when unset/undecryptable."""
        if not self.fileserver_package_read_token_encrypted:
            return ""
        try:
            return _fernet().decrypt(self.fileserver_package_read_token_encrypted.encode()).decode()
        except Exception:  # noqa: BLE001 - treat any decrypt failure as "no token"
            return ""
