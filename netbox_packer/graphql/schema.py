import strawberry
import strawberry_django

from .types import (
    PackerBuildTargetType,
    PackerBuildType,
    PackerInstallerConfigType,
    PackerTemplateType,
)


@strawberry.type(name="Query")
class NetBoxPackerQuery:
    packer_template: PackerTemplateType = strawberry_django.field()
    packer_template_list: list[PackerTemplateType] = strawberry_django.field()

    packer_build: PackerBuildType = strawberry_django.field()
    packer_build_list: list[PackerBuildType] = strawberry_django.field()

    packer_installer_config: PackerInstallerConfigType = strawberry_django.field()
    packer_installer_config_list: list[PackerInstallerConfigType] = strawberry_django.field()

    packer_build_target: PackerBuildTargetType = strawberry_django.field()
    packer_build_target_list: list[PackerBuildTargetType] = strawberry_django.field()
