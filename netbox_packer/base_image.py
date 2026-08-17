"""Pure helpers for comparing desired and successfully baked base-image sources."""


def pin_differs_from_built_source(
    *,
    desired_url: str,
    desired_sha256: str,
    built_url: str,
    built_sha256: str,
) -> bool:
    """Return whether the declared pin differs from the last successful bake.

    An empty desired pin retains the legacy mutable-release behavior. It matches an
    empty historical snapshot, and it matches a newly recorded unpinned build (whose
    resolved URL is non-empty but whose digest is empty). A non-empty digest in the
    built snapshot proves that a template pin or per-build override was used, so
    clearing the desired pin makes that artifact stale.
    """

    desired_url = (desired_url or "").strip()
    desired_sha256 = (desired_sha256 or "").strip().lower()
    built_url = (built_url or "").strip()
    built_sha256 = (built_sha256 or "").strip().lower()

    if desired_url:
        return desired_url != built_url or desired_sha256 != built_sha256
    if desired_sha256:
        return desired_sha256 != built_sha256
    return bool(built_sha256)


# Only the cloud_config builder resolves a vendor base image, forwards its digest for
# verification, and records the at-build snapshot. Keep this name in step with
# PackerInstallerConfig.INSTALLER_TYPE_CHOICES.
PIN_CAPABLE_INSTALLER_TYPES = frozenset({"cloud_config"})


def base_image_pin_applies(installer_type: str | None) -> bool:
    """Return whether a base-image pin is enforceable for this installer type.

    A pin is only meaningful where the builder actually consumes it. The local Packer
    path builds from HCL inputs, never resolves a base image URL, and writes no at-build
    snapshot — so honouring a pin there would report the template stale forever (desired
    set, built permanently empty), and `auto_rebuild` would turn that into an endless
    rebuild loop. Accepting such a pin is also worse than useless on its own terms: it
    looks like the base image is verified while nothing enforces it.
    """

    return (installer_type or "").strip() in PIN_CAPABLE_INSTALLER_TYPES
