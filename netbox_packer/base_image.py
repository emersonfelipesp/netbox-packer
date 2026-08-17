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
