from typing import Annotated

import strawberry
import strawberry_django
from netbox.graphql.types import NetBoxObjectType

from .. import models


@strawberry_django.type(models.PackerInstallerConfig, fields="__all__")
class PackerInstallerConfigType(NetBoxObjectType):
    templates: list[Annotated["PackerTemplateType", strawberry.lazy("netbox_packer.graphql.types")]]


@strawberry_django.type(models.PackerTemplate, fields="__all__")
class PackerTemplateType(NetBoxObjectType):
    installer_config: (
        Annotated[
            "PackerInstallerConfigType",
            strawberry.lazy("netbox_packer.graphql.types"),
        ]
        | None
    )
    builds: list[Annotated["PackerBuildType", strawberry.lazy("netbox_packer.graphql.types")]]
    build_targets: list[Annotated["PackerBuildTargetType", strawberry.lazy("netbox_packer.graphql.types")]]


@strawberry_django.type(models.PackerBuild, fields="__all__")
class PackerBuildType(NetBoxObjectType):
    template: Annotated["PackerTemplateType", strawberry.lazy("netbox_packer.graphql.types")]


@strawberry_django.type(models.PackerBuildTarget, fields="__all__")
class PackerBuildTargetType(NetBoxObjectType):
    template: Annotated["PackerTemplateType", strawberry.lazy("netbox_packer.graphql.types")]
