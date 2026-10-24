#!/usr/bin/env python3
"""Build, resume, publish, and verify immutable release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
Transport = Callable[[urllib.request.Request, int], tuple[int, bytes]]


class ReleaseArtifactError(ValueError):
    """Release bytes or registry metadata violate the reviewed contract."""


class RegistryStatusError(ReleaseArtifactError):
    """The registry returned an explicit unexpected HTTP status."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def validate_registry_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    invalid = (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or bool(parsed.query)
        or bool(parsed.fragment)
    )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ReleaseArtifactError("Registry origin must be an HTTPS origin") from exc
    if invalid:
        raise ReleaseArtifactError("Registry origin must be an HTTPS origin")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"https://{host}{f':{port}' if port is not None else ''}"


def _record(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or SAFE_NAME_RE.fullmatch(path.name) is None:
        raise ReleaseArtifactError("Release artifact is not a safe regular file")
    size = path.stat().st_size
    if not 0 < size <= MAX_ARTIFACT_BYTES:
        raise ReleaseArtifactError("Release artifact size is invalid")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {"name": path.name, "sha256": digest.hexdigest(), "size": size}


def _manifest_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _expected_names(version: str) -> tuple[str, str]:
    return (f"netbox_packer-{version}-py3-none-any.whl", f"netbox_packer-{version}.tar.gz")


def _release_files(dist: Path, version: str) -> list[Path]:
    files = sorted(path for path in dist.iterdir() if path.is_file() and path.name != ".gitignore")
    if [path.name for path in files] != sorted(_expected_names(version)):
        raise ReleaseArtifactError("Release artifact filenames are invalid")
    return files


def _validate_version(version: str) -> None:
    pattern = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:rc[1-9][0-9]*|\.post[1-9][0-9]*)?"
    if re.fullmatch(pattern, version) is None:
        raise ReleaseArtifactError("Version is not a supported release version")


def create_manifest(*, dist: Path, version: str, source_sha: str) -> dict[str, Any]:
    if SHA_RE.fullmatch(source_sha) is None:
        raise ReleaseArtifactError("Source SHA must be lowercase 40-hex")
    _validate_version(version)
    return {
        "artifacts": [_record(path) for path in _release_files(dist, version)],
        "package": "netbox-packer",
        "schema": 1,
        "source_sha": source_sha,
        "version": version,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise ReleaseArtifactError("Manifest size is invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseArtifactError("Manifest is not valid JSON") from exc
    keys = {"artifacts", "package", "schema", "source_sha", "version"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseArtifactError("Manifest schema is not exact")
    if value.get("schema") != 1 or value.get("package") != "netbox-packer":
        raise ReleaseArtifactError("Manifest identity is invalid")
    if _manifest_bytes(value) != raw:
        raise ReleaseArtifactError("Manifest is not canonical JSON")
    return value


def write_manifest(*, dist: Path, version: str, source_sha: str, output: Path) -> dict[str, Any]:
    manifest = create_manifest(dist=dist, version=version, source_sha=source_sha)
    output.write_bytes(_manifest_bytes(manifest))
    return manifest


def verify_manifest(*, manifest_path: Path, dist: Path, version: str, source_sha: str) -> dict[str, Any]:
    actual = load_manifest(manifest_path)
    expected = create_manifest(dist=dist, version=version, source_sha=source_sha)
    if actual != expected:
        raise ReleaseArtifactError("Manifest does not match the artifact bytes")
    return actual


def _quoted(value: str) -> str:
    if SAFE_NAME_RE.fullmatch(value) is None:
        raise ReleaseArtifactError("Registry identity is unsafe")
    return urllib.parse.quote(value, safe="")


def _default_transport(request: urllib.request.Request, maximum: int) -> tuple[int, bytes]:
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
            body = response.read(maximum + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read(maximum + 1)
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReleaseArtifactError("Registry request failed") from exc
    if len(body) > maximum:
        raise ReleaseArtifactError("Registry response exceeds its size bound")
    return status, body


class RegistryClient:
    """Bounded, no-redirect Gitea package client."""

    def __init__(self, origin: str, token: str, transport: Transport | None = None) -> None:
        self.origin = origin
        self.token = token
        self.transport = transport or _default_transport

    def _request(self, method: str, path: str, data: bytes | None) -> urllib.request.Request:
        url = f"{self.origin}{path}"
        parsed = urllib.parse.urlsplit(url)
        if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            raise ReleaseArtifactError("Registry origin is not canonical")
        headers = {"Accept": "application/json", "User-Agent": "netbox-packer-release/1"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        return urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310

    def request(
        self, method: str, path: str, *, data: bytes | None = None, maximum: int = MAX_METADATA_BYTES
    ) -> bytes | None:
        status, body = self.transport(self._request(method, path, data), maximum)
        if status == 404:
            return None
        if status != 200:
            raise RegistryStatusError(f"Registry returned HTTP {status}")
        return body

    def json(self, path: str) -> object | None:
        body = self.request("GET", path)
        if body is None:
            return None
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReleaseArtifactError("Registry response is not valid JSON") from exc
        if value is None:
            # Only an HTTP 404 means absent; a successful null body is malformed.
            raise ReleaseArtifactError("Registry returned an empty JSON document")
        return value

    def expect(self, method: str, path: str, *, data: bytes | None, statuses: set[int]) -> int:
        status, _body = self.transport(self._request(method, path, data), MAX_METADATA_BYTES)
        if status not in statuses:
            raise RegistryStatusError(f"Registry returned HTTP {status}")
        return status


def _public_request(url: str, maximum: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "netbox-packer-release/1"}
    )  # noqa: S310
    status, body = _default_transport(request, maximum)
    if status != 200:
        raise ReleaseArtifactError(f"Public index returned HTTP {status}")
    return body


def check_public_index(
    version: str, *, request_fn: Callable[[str, int], bytes] = _public_request
) -> list[dict[str, str]]:
    _validate_version(version)
    origin = "https://test.pypi.org" if "rc" in version else "https://pypi.org"
    url = f"{origin}/pypi/netbox-packer/{urllib.parse.quote(version, safe='')}/json"
    try:
        raw = request_fn(url, MAX_INDEX_BYTES)
        if len(raw) > MAX_INDEX_BYTES:
            raise ReleaseArtifactError("Public index response exceeds its size bound")
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReleaseArtifactError("Public index response is not valid JSON") from exc
    info = value.get("info") if isinstance(value, dict) else None
    urls = value.get("urls") if isinstance(value, dict) else None
    if not isinstance(info, dict) or canonical_name(str(info.get("name", ""))) != "netbox-packer":
        raise ReleaseArtifactError("Public index package name is invalid")
    if info.get("version") != version:
        raise ReleaseArtifactError("Public index package version is invalid")
    if not isinstance(urls, list) or not urls:
        raise ReleaseArtifactError("Public index release has no files")
    return [_public_file(item) for item in urls]


def _public_file(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ReleaseArtifactError("Public index file metadata is invalid")
    name = value.get("filename")
    digests = value.get("digests")
    digest = digests.get("sha256") if isinstance(digests, dict) else None
    if not isinstance(name, str) or SAFE_NAME_RE.fullmatch(name) is None:
        raise ReleaseArtifactError("Public index file metadata is invalid")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise ReleaseArtifactError("Public index file metadata is invalid")
    return {"filename": name, "sha256": digest}


def _package_path(owner: str, package_type: str, package: str, version: str) -> str:
    values = map(_quoted, (owner, package_type, package, version))
    return "/api/v1/packages/" + "/".join(values)


def _repository_name(metadata: object) -> str | None:
    repository = metadata.get("repository") if isinstance(metadata, dict) else None
    if repository is None:
        return None
    if not isinstance(repository, dict) or not isinstance(repository.get("full_name"), str):
        raise ReleaseArtifactError("Package repository association is invalid")
    return repository["full_name"]


def _validate_package(
    metadata: object, *, package: str, version: str, package_type: str, repository: str, require_link: bool
) -> None:
    identity = (
        metadata.get("name") if isinstance(metadata, dict) else None,
        metadata.get("version") if isinstance(metadata, dict) else None,
        metadata.get("type") if isinstance(metadata, dict) else None,
    )
    if identity != (package, version, package_type):
        raise ReleaseArtifactError("Package identity is invalid")
    linked = _repository_name(metadata)
    if linked is not None and linked != repository:
        raise ReleaseArtifactError("Package is linked to a different repository")
    if require_link and linked != repository:
        raise ReleaseArtifactError("Package is not linked to the required repository")


def _inventory(files: object, expected: set[str], *, exact: bool) -> dict[str, tuple[int, str]]:
    if not isinstance(files, list):
        raise ReleaseArtifactError("Package file inventory is invalid")
    result: dict[str, tuple[int, str]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ReleaseArtifactError("Package file inventory is invalid")
        name, size, digest = item.get("name"), item.get("size"), item.get("sha256")
        valid = (
            isinstance(name, str)
            and name in expected
            and name not in result
            and isinstance(size, int)
            and not isinstance(size, bool)
            and 0 < size <= MAX_ARTIFACT_BYTES
            and isinstance(digest, str)
            and DIGEST_RE.fullmatch(digest) is not None
        )
        if not valid:
            raise ReleaseArtifactError("Package file inventory is invalid")
        result[name] = (size, digest)
    if exact and set(result) != expected:
        raise ReleaseArtifactError("Package file inventory is not exact")
    return result


def _remote_package(
    client: RegistryClient,
    *,
    owner: str,
    package_type: str,
    package: str,
    version: str,
    repository: str,
    require_link: bool,
) -> tuple[object, object] | None:
    base = _package_path(owner, package_type, package, version)
    metadata = client.json(base)
    if metadata is None:
        return None
    _validate_package(
        metadata,
        package=package,
        version=version,
        package_type=package_type,
        repository=repository,
        require_link=require_link,
    )
    files = client.json(f"{base}/files")
    if files is None:
        raise ReleaseArtifactError("Package file inventory is absent")
    return metadata, files


def _download(client: RegistryClient, path: str, *, size: int, digest: str, maximum: int = MAX_ARTIFACT_BYTES) -> bytes:
    content = client.request("GET", path, maximum=maximum)
    if content is None:
        raise ReleaseArtifactError("Registry file is absent")
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise ReleaseArtifactError("Registry file differs from its inventory")
    return content


def _pypi_download_path(owner: str, version: str, name: str) -> str:
    return f"/api/packages/{_quoted(owner)}/pypi/files/netbox-packer/{_quoted(version)}/{_quoted(name)}"


def _generic_download_path(owner: str, version: str) -> str:
    return (
        f"/api/packages/{_quoted(owner)}/generic/netbox-packer-release-manifest/"
        f"{_quoted(version)}/release-manifest.json"
    )


def _client(registry_origin: str, transport: Transport | None = None) -> RegistryClient:
    return RegistryClient(validate_registry_origin(registry_origin), os.getenv("GITEA_PACKAGE_TOKEN", ""), transport)


def plan_publication(
    *,
    registry_origin: str,
    owner: str,
    repository: str,
    version: str,
    source_sha: str,
    dist: Path,
    manifest_path: Path,
    upload_list: Path,
    transport: Transport | None = None,
) -> dict[str, Any]:
    # The build-step manifest is the source of truth: it must describe exactly
    # the locally built artifacts, and it is never regenerated here.
    manifest = verify_manifest(manifest_path=manifest_path, dist=dist, version=version, source_sha=source_sha)
    local = {item["name"]: item["sha256"] for item in manifest["artifacts"]}
    client = _client(registry_origin, transport)
    # require_link=False: an interrupted run can leave the package uploaded but
    # not yet linked. A package linked to any other repository still fails
    # here, and finish_publication requires the exact link before success.
    remote = _remote_package(
        client,
        owner=owner,
        package_type="pypi",
        package="netbox-packer",
        version=version,
        repository=f"{owner}/{repository}",
        require_link=False,
    )
    inventory = {} if remote is None else _inventory(remote[1], set(local), exact=False)
    uploads: list[Path] = []
    for name in sorted(local):
        if name not in inventory:
            uploads.append((dist / name).resolve())
            continue
        size, digest = inventory[name]
        content = _download(client, _pypi_download_path(owner, version, name), size=size, digest=digest)
        # Registry bytes never replace local build output. An existing remote
        # file must be byte-identical to the artifact this run built.
        if hashlib.sha256(content).hexdigest() != local[name]:
            raise ReleaseArtifactError(
                "Remote artifact differs from the local build; resolve the partial publication before retrying"
            )
    _check_existing_manifest(client, owner, repository, version, manifest_path.read_bytes())
    upload_list.write_text("".join(f"{path}\n" for path in uploads), encoding="utf-8")
    return manifest


def _check_existing_manifest(
    client: RegistryClient, owner: str, repository: str, version: str, expected: bytes
) -> None:
    remote = _remote_package(
        client,
        owner=owner,
        package_type="generic",
        package="netbox-packer-release-manifest",
        version=version,
        repository=f"{owner}/{repository}",
        require_link=False,
    )
    if remote is None:
        return
    inventory = _inventory(remote[1], {"release-manifest.json"}, exact=True)
    size, digest = inventory["release-manifest.json"]
    actual = _download(
        client, _generic_download_path(owner, version), size=size, digest=digest, maximum=MAX_MANIFEST_BYTES
    )
    if actual != expected:
        raise ReleaseArtifactError("Remote release manifest differs from the computed manifest")


def _link_package(
    client: RegistryClient, *, owner: str, repository: str, package_type: str, package: str, version: str
) -> None:
    base = _package_path(owner, package_type, package, version)
    path = f"/api/v1/packages/{_quoted(owner)}/{_quoted(package_type)}/{_quoted(package)}/-/link/{_quoted(repository)}"
    try:
        client.expect("POST", path, data=b"", statuses={201, 204})
        return
    except RegistryStatusError:
        metadata = client.json(base)
    if metadata is None:
        raise ReleaseArtifactError("Package disappeared while linking")
    _validate_package(
        metadata,
        package=package,
        version=version,
        package_type=package_type,
        repository=f"{owner}/{repository}",
        require_link=True,
    )


def _verify_pypi(
    client: RegistryClient,
    owner: str,
    repository: str,
    version: str,
    manifest: dict[str, Any],
    dist: Path,
    replace_local: bool,
) -> None:
    remote = _remote_package(
        client,
        owner=owner,
        package_type="pypi",
        package="netbox-packer",
        version=version,
        repository=f"{owner}/{repository}",
        require_link=True,
    )
    if remote is None:
        raise ReleaseArtifactError("PyPI package is absent")
    expected = {item["name"]: (item["size"], item["sha256"]) for item in manifest["artifacts"]}
    inventory = _inventory(remote[1], set(expected), exact=True)
    if inventory != expected:
        raise ReleaseArtifactError("PyPI package inventory differs from the release manifest")
    for name, (size, digest) in expected.items():
        content = _download(client, _pypi_download_path(owner, version, name), size=size, digest=digest)
        path = dist / name
        if replace_local:
            path.write_bytes(content)
        elif not path.is_file() or path.read_bytes() != content:
            raise ReleaseArtifactError("Remote artifact differs from the local artifact")


def _verify_remote(
    client: RegistryClient,
    *,
    owner: str,
    repository: str,
    version: str,
    source_sha: str,
    dist: Path,
    manifest_path: Path,
    replace_local: bool,
) -> dict[str, Any]:
    generic = _remote_package(
        client,
        owner=owner,
        package_type="generic",
        package="netbox-packer-release-manifest",
        version=version,
        repository=f"{owner}/{repository}",
        require_link=True,
    )
    if generic is None:
        raise ReleaseArtifactError("Release-manifest package is absent")
    generic_inventory = _inventory(generic[1], {"release-manifest.json"}, exact=True)
    size, digest = generic_inventory["release-manifest.json"]
    raw_manifest = _download(
        client, _generic_download_path(owner, version), size=size, digest=digest, maximum=MAX_MANIFEST_BYTES
    )
    if replace_local:
        manifest_path.write_bytes(raw_manifest)
    elif manifest_path.read_bytes() != raw_manifest:
        raise ReleaseArtifactError("Remote release manifest differs from the local manifest")
    manifest = load_manifest(manifest_path)
    if manifest.get("version") != version or manifest.get("source_sha") != source_sha:
        raise ReleaseArtifactError("Release manifest source identity is invalid")
    _verify_pypi(client, owner, repository, version, manifest, dist, replace_local)
    return verify_manifest(manifest_path=manifest_path, dist=dist, version=version, source_sha=source_sha)


def fetch_gitea_artifacts(
    *,
    registry_origin: str,
    owner: str,
    repository: str,
    version: str,
    source_sha: str,
    dist: Path,
    manifest_path: Path,
    transport: Transport | None = None,
) -> dict[str, Any]:
    client = _client(registry_origin, transport)
    dist.mkdir(mode=0o700)
    return _verify_remote(
        client,
        owner=owner,
        repository=repository,
        version=version,
        source_sha=source_sha,
        dist=dist,
        manifest_path=manifest_path,
        replace_local=True,
    )


def finish_publication(
    *,
    registry_origin: str,
    owner: str,
    repository: str,
    version: str,
    source_sha: str,
    dist: Path,
    manifest_path: Path,
    transport: Transport | None = None,
) -> dict[str, Any]:
    verify_manifest(manifest_path=manifest_path, dist=dist, version=version, source_sha=source_sha)
    client = _client(registry_origin, transport)
    remote = _remote_package(
        client,
        owner=owner,
        package_type="generic",
        package="netbox-packer-release-manifest",
        version=version,
        repository=f"{owner}/{repository}",
        require_link=False,
    )
    if remote is None:
        client.expect("PUT", _generic_download_path(owner, version), data=manifest_path.read_bytes(), statuses={201})
    else:
        _check_existing_manifest(client, owner, repository, version, manifest_path.read_bytes())
    for package_type, package in (("pypi", "netbox-packer"), ("generic", "netbox-packer-release-manifest")):
        _link_package(
            client,
            owner=owner,
            repository=repository,
            package_type=package_type,
            package=package,
            version=version,
        )
    return _verify_remote(
        client,
        owner=owner,
        repository=repository,
        version=version,
        source_sha=source_sha,
        dist=dist,
        manifest_path=manifest_path,
        replace_local=False,
    )


def _add_registry_arguments(command: argparse.ArgumentParser, *, upload_list: bool = False) -> None:
    command.add_argument("--registry-origin", required=True)
    command.add_argument("--owner", required=True)
    command.add_argument("--repository", required=True)
    command.add_argument("--version", required=True)
    command.add_argument("--source-sha", required=True)
    command.add_argument("--dist", type=Path, required=True)
    command.add_argument("--manifest", type=Path, required=True)
    if upload_list:
        command.add_argument("--upload-list", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--dist", type=Path, required=True)
        command.add_argument("--version", required=True)
        command.add_argument("--source-sha", required=True)
        command.add_argument("--manifest", type=Path, required=True)
    public = commands.add_parser("public-index")
    public.add_argument("--version", required=True)
    origin = commands.add_parser("registry-origin")
    origin.add_argument("--value", required=True)
    _add_registry_arguments(commands.add_parser("fetch-gitea"))
    _add_registry_arguments(commands.add_parser("plan"), upload_list=True)
    _add_registry_arguments(commands.add_parser("finish"))
    return parser


def _run_registry_command(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        "registry_origin": args.registry_origin,
        "owner": args.owner,
        "repository": args.repository,
        "version": args.version,
        "source_sha": args.source_sha,
        "dist": args.dist,
        "manifest_path": args.manifest,
    }
    if args.command == "plan":
        return plan_publication(**values, upload_list=args.upload_list)
    if args.command == "finish":
        return finish_publication(**values)
    return fetch_gitea_artifacts(**values)


def main() -> None:
    args = _parser().parse_args()
    if args.command == "registry-origin":
        print(validate_registry_origin(args.value))
        return
    if args.command == "public-index":
        for item in check_public_index(args.version):
            print(f"{item['filename']} sha256={item['sha256']}")
        return
    if args.command == "manifest":
        manifest = write_manifest(
            dist=args.dist, version=args.version, source_sha=args.source_sha, output=args.manifest
        )
    elif args.command == "verify":
        manifest = verify_manifest(
            manifest_path=args.manifest, dist=args.dist, version=args.version, source_sha=args.source_sha
        )
    else:
        manifest = _run_registry_command(args)
    print(hashlib.sha256(_manifest_bytes(manifest)).hexdigest())


if __name__ == "__main__":
    main()
