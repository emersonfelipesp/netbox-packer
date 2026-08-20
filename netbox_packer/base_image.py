"""Pure helpers for validating and comparing cloud base-image sources."""

from urllib.parse import urlsplit, urlunsplit


def validate_base_image_url(value: str, *, source: str = "Base image URL") -> str:
    """Return a credential-free base-image URL or raise ``ValueError``.

    Vendor image artifacts do not need URL userinfo, query parameters, or
    fragments. Refusing all three structurally closes inline credential paths
    without trying to maintain a list of every provider-specific secret name.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source} must be a non-empty URL.")
    candidate = value.strip()
    try:
        parts = urlsplit(candidate)
        # Accessing hostname/port also rejects malformed bracketed hosts and ports.
        hostname = parts.hostname
        _ = parts.port
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} must be a structurally valid URL.") from exc
    if not parts.scheme or not hostname:
        raise ValueError(f"{source} must be a structurally valid URL.")
    has_query_delimiter = "?" in candidate.split("#", 1)[0]
    has_fragment_delimiter = "#" in candidate
    if (
        parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or has_query_delimiter
        or has_fragment_delimiter
    ):
        raise ValueError(
            f"{source} must not contain userinfo, query parameters, or fragments. "
            "Authenticated downloads require an opaque secret reference, not inline URL credentials."
        )
    return candidate


def redact_base_image_url(value: str) -> str:
    """Remove URL userinfo, query, and fragment data before persistence."""

    if not isinstance(value, str) or not value.strip():
        return "[REDACTED INVALID BASE IMAGE URL]"
    candidate = value.strip()
    try:
        return validate_base_image_url(candidate)
    except ValueError:
        pass
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return "[REDACTED INVALID BASE IMAGE URL]"
    if not parts.scheme or not hostname:
        return "[REDACTED INVALID BASE IMAGE URL]"
    safe_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        safe_host = f"{safe_host}:{port}"
    return urlunsplit((parts.scheme, safe_host, parts.path, "", ""))


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
