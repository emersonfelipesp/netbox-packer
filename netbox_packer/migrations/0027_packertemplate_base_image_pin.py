"""Add the reproducible, verifiable base-image pin to ``PackerTemplate``.

Without these fields a bake resolves the vendor's mutable ``latest`` directory and
sends no content digest, so rebuilding the same profile can silently produce a
different root filesystem and the artifact that becomes the guest's entire operating
system is accepted with no integrity check.

Both fields default to empty, so every existing template keeps its current behaviour
and payload. A template becomes *pinned* only when an operator sets them, and a pinned
URL without a digest is refused at build time rather than being trusted.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0026_harden_influxdb_0020_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="packertemplate",
            name="base_image_url",
            field=models.URLField(
                blank=True,
                default="",
                max_length=500,
                help_text=(
                    "Optional exact base image URL, pinning this template to a "
                    "specific vendor artifact instead of the release default. "
                    "Requires Base image sha256."
                ),
            ),
        ),
        migrations.AddField(
            model_name="packertemplate",
            name="base_image_sha256",
            field=models.CharField(
                blank=True,
                default="",
                max_length=64,
                validators=[
                    django.core.validators.RegexValidator(
                        regex=r"^[0-9a-f]{64}$",
                        message=("Enter a lowercase 64-character hexadecimal sha256 digest."),
                    )
                ],
                help_text=(
                    "Reviewed sha256 of the base image, verified by proxbox-api after "
                    "download. Record where the digest was obtained and verified; an "
                    "unverified digest proves nothing."
                ),
            ),
        ),
    ]
