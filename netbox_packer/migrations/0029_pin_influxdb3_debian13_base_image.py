"""Pin the Debian 13 InfluxDB 3 Core template to a verified base image.

Migration ``0027`` added the pin fields and ``0028`` added their at-build snapshots,
but no profile used them — so the mechanism reduced exposure for exactly zero
templates. This migration pins one profile, and one only.

**Why this profile.** ``influxdb-core-3.11.0-debian-13`` (VMID 9052) is the newest
seeded profile and has no baked artifact yet, so pinning it invalidates nothing that
already exists. Pinning a profile whose template is already ``ready`` marks it pending
for rebake (see ``PackerTemplate.is_stale`` and ``pin_differs_from_built_source``), and
doing that across the seeded catalog would demand estate-wide rebakes — an operator
decision, not a migration's.

**How the digest was obtained.** Without the pin, ``_derive_release_image_url()``
resolves the *mutable* ``.../trixie/latest/debian-13-genericcloud-amd64.qcow2``, whose
contents change whenever Debian publishes a new snapshot. The pin names the dated
snapshot instead.

Debian publishes **only** ``SHA512SUMS`` for cloud images — there is no ``SHA256SUMS``
— and a SHA-256 cannot be derived from a SHA-512, so the digest below was produced by
hashing the artifact locally after verifying it:

1. fetch ``SHA512SUMS`` from the dated snapshot directory over HTTPS;
2. download the exact dated ``.qcow2`` (337,707,008 bytes);
3. confirm its SHA-512 equals the published value — this is what proves the hashed
   bytes are the bytes Debian published a checksum for;
4. only then compute the SHA-256 recorded here.

The published SHA-512 that step 3 matched was
``2d921fce234cfa145c7d819d3ef7b2dba2fe84ac436fb2fff78ade3424b52f6c42f85e8fc97e253c77884c762caa2e52c6914946f75d8ebb231320b0a1f42cc4``.

**Stated limit, deliberately not papered over:** the snapshot directory carries no
GPG signature (no ``SHA512SUMS.sign``), so this trust chain is TLS to
``cloud.debian.org`` plus the published checksum — stronger than an unpinned mutable
URL, weaker than an offline-verifiable Debian signature.

Never refresh this pin by editing the constants alone. Repeat the whole procedure
against the new snapshot; a digest copied from a listing without hashing the artifact
proves only that the listing and the field agree.

An operator who has already pinned this row by hand is left alone — the write is a
compare-and-set against the unpinned state, and a row pinned to something else is
reported rather than overwritten.
"""

from django.db import migrations

TEMPLATE_NAME = "influxdb-core-3.11.0-debian-13"

PINNED_IMAGE_URL = (
    "https://cloud.debian.org/images/cloud/trixie/20260509-2473/"
    "debian-13-genericcloud-amd64-20260509-2473.qcow2"
)
PINNED_IMAGE_SHA256 = "34f5481f320aef28408720a861582dcfe3a81781ee69f3910a64c29ad5395b89"


def pin_influxdb3_debian13_base_image(apps, schema_editor):
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    template = (
        PackerTemplate.objects.select_for_update().filter(name=TEMPLATE_NAME).first()
    )
    if template is None:
        # The 0025 seed is skipped on deployments that already owned the VMID, so a
        # missing row is legitimate rather than an error.
        return

    if template.base_image_url or template.base_image_sha256:
        if (
            template.base_image_url == PINNED_IMAGE_URL
            and template.base_image_sha256 == PINNED_IMAGE_SHA256
        ):
            return
        raise RuntimeError(
            f"Refusing to overwrite an existing base image pin on {TEMPLATE_NAME!r}: "
            f"url={template.base_image_url!r} sha256={template.base_image_sha256!r}. "
            "An operator pinned this row to a different artifact, and replacing it "
            "would silently change the base image every future clone is built from. "
            "Reconcile by hand (see docs/cloud-init-template-images.md -> Base Image "
            "Pinning), then rerun this migration."
        )

    # Compare-and-set against the unpinned state: a row pinned concurrently is not
    # overwritten, the update simply affects no rows.
    PackerTemplate.objects.filter(
        pk=template.pk, base_image_url="", base_image_sha256=""
    ).update(
        base_image_url=PINNED_IMAGE_URL,
        base_image_sha256=PINNED_IMAGE_SHA256,
    )


def unpin_influxdb3_debian13_base_image(apps, schema_editor):
    # Intentionally a no-op. Rolling back must not silently return the template to the
    # mutable `latest` directory, which is the unverified state this migration exists
    # to remove. Clear the two fields by hand if that is genuinely intended.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0028_base_image_build_snapshots"),
    ]

    operations = [
        migrations.RunPython(
            pin_influxdb3_debian13_base_image,
            unpin_influxdb3_debian13_base_image,
        ),
    ]
