import django_tables2 as tables
from netbox.tables import ChoiceFieldColumn, NetBoxTable

from .models import PackerBuild, PackerBuildTarget, PackerInstallerConfig, PackerTemplate


class PackerTemplateTable(NetBoxTable):
    name = tables.Column(linkify=True)
    os_family = ChoiceFieldColumn()
    build_status = ChoiceFieldColumn()
    cloud_init_ready = tables.BooleanColumn()
    create_instance = tables.TemplateColumn(
        template_name="netbox_packer/inc/create_instance_button.html",
        verbose_name="Create new instance",
        orderable=False,
    )

    class Meta(NetBoxTable.Meta):
        model = PackerTemplate
        fields = (
            "pk",
            "id",
            "name",
            "os_family",
            "os_version",
            "proxmox_node",
            "build_status",
            "built_at",
            "cloud_init_ready",
            "auto_rebuild",
            "create_instance",
            "description",
            "actions",
        )
        default_columns = (
            "name",
            "os_family",
            "os_version",
            "proxmox_node",
            "build_status",
            "built_at",
            "cloud_init_ready",
            "create_instance",
            "actions",
        )


class PackerBuildTable(NetBoxTable):
    template = tables.Column(linkify=True)
    id = tables.Column(linkify=True, verbose_name="Build ID")

    class Meta(NetBoxTable.Meta):
        model = PackerBuild
        fields = (
            "pk",
            "id",
            "template",
            "status",
            "queued_at",
            "started_at",
            "finished_at",
            "triggered_by",
            "selected_node",
            "exit_code",
            "actions",
        )
        default_columns = (
            "id",
            "template",
            "status",
            "queued_at",
            "started_at",
            "finished_at",
            "selected_node",
            "actions",
        )


class PackerInstallerConfigTable(NetBoxTable):
    name = tables.Column(linkify=True)
    os_family = ChoiceFieldColumn()

    class Meta(NetBoxTable.Meta):
        model = PackerInstallerConfig
        fields = (
            "pk",
            "id",
            "name",
            "os_family",
            "installer_type",
            "version",
            "checksum",
            "description",
            "actions",
        )
        default_columns = (
            "name",
            "os_family",
            "installer_type",
            "version",
            "checksum",
            "actions",
        )


class PackerBuildTargetTable(NetBoxTable):
    template = tables.Column(linkify=True)
    enabled = tables.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = PackerBuildTarget
        fields = (
            "pk",
            "id",
            "template",
            "proxmox_node",
            "priority",
            "enabled",
            "actions",
        )
        default_columns = (
            "template",
            "proxmox_node",
            "priority",
            "enabled",
            "actions",
        )
