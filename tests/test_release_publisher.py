"""Contracts for the dispatch-only package-first Gitea publisher."""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".gitea/workflows/publish-gitea.yml"
HELPER = ROOT / "scripts/release_artifacts.py"
VALIDATOR = ROOT / "scripts/validate_release_ref.py"
GIT = "/usr/bin/git"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper():
    return _module(HELPER, "release_artifacts_test")


@pytest.fixture
def validator():
    return _module(VALIDATOR, "validate_release_ref_test")


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        [GIT, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    _git(repository, "config", "user.name", "Release Test")
    return repository


def _commit_version(repository: Path, version: str, message: str) -> str:
    (repository / "pyproject.toml").write_text(f'[project]\nname = "netbox-packer"\nversion = "{version}"\n')
    _git(repository, "add", "pyproject.toml")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("tag", "event", "accepted"),
    [
        ("v1.2.3rc1", "rc", True),
        ("v1.2.3rc10", "rc", True),
        ("v1.2.3", "final", True),
        ("v1.2.3.post1", "final", True),
        ("1.2.3", "final", False),
        ("v01.2.3", "final", False),
        ("v1.2.3rc0", "rc", False),
        ("v1.2.3rc1", "final", False),
        ("v1.2.3.post0", "final", False),
        ("v1.2.3.post1", "rc", False),
    ],
)
def test_tag_regex_matrix(validator, tag: str, event: str, accepted: bool) -> None:
    assert bool(validator._tag_pattern(event).fullmatch(tag)) is accepted


def test_release_ref_rejects_tag_outside_main(tmp_path: Path, validator) -> None:
    repository = _repository(tmp_path)
    main_sha = _commit_version(repository, "0.0.9", "main")
    _git(repository, "switch", "-c", "unreviewed")
    _commit_version(repository, "0.1.0", "unreviewed")
    _git(repository, "tag", "v0.1.0")
    _git(repository, "update-ref", "refs/release-policy/candidate", "v0.1.0")
    _git(repository, "update-ref", "refs/release-policy/main", main_sha)
    with pytest.raises(validator.ReleaseRefError, match="not on canonical main"):
        validator.validate_release_ref(repository=repository, tag="v0.1.0", event="final")


def test_release_ref_rejects_pyproject_version_mismatch(tmp_path: Path, validator) -> None:
    repository = _repository(tmp_path)
    main_sha = _commit_version(repository, "0.1.1", "reviewed")
    _git(repository, "tag", "v0.1.0")
    _git(repository, "update-ref", "refs/release-policy/candidate", "v0.1.0")
    _git(repository, "update-ref", "refs/release-policy/main", main_sha)
    with pytest.raises(validator.ReleaseRefError, match="differs from the tag"):
        validator.validate_release_ref(repository=repository, tag="v0.1.0", event="final")


def _dist(path: Path) -> Path:
    path.mkdir()
    (path / "netbox_packer-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (path / "netbox_packer-0.1.0.tar.gz").write_bytes(b"sdist")
    return path


def test_manifest_round_trip(tmp_path: Path, helper) -> None:
    dist = _dist(tmp_path / "dist")
    output = tmp_path / "release-manifest.json"
    expected = helper.write_manifest(dist=dist, version="0.1.0", source_sha="a" * 40, output=output)
    assert expected["package"] == "netbox-packer"
    assert helper.verify_manifest(manifest_path=output, dist=dist, version="0.1.0", source_sha="a" * 40) == expected


def test_manifest_rejects_extra_file(tmp_path: Path, helper) -> None:
    dist = _dist(tmp_path / "dist")
    (dist / "extra.txt").write_bytes(b"extra")
    with pytest.raises(helper.ReleaseArtifactError, match="filenames"):
        helper.create_manifest(dist=dist, version="0.1.0", source_sha="a" * 40)


def test_manifest_rejects_renamed_artifact(tmp_path: Path, helper) -> None:
    dist = _dist(tmp_path / "dist")
    (dist / "netbox_packer-0.1.0.tar.gz").rename(dist / "netbox_packer-0.2.0.tar.gz")
    with pytest.raises(helper.ReleaseArtifactError, match="filenames"):
        helper.create_manifest(dist=dist, version="0.1.0", source_sha="a" * 40)


def test_manifest_rejects_unrelated_artifact_with_version_substring(tmp_path: Path, helper) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "unrelated-10.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "unrelated-10.1.0.tar.gz").write_bytes(b"sdist")
    with pytest.raises(helper.ReleaseArtifactError, match="filenames"):
        helper.create_manifest(dist=dist, version="0.1.0", source_sha="a" * 40)


def test_manifest_rejects_noncanonical_json(tmp_path: Path, helper) -> None:
    output = tmp_path / "release-manifest.json"
    value = helper.create_manifest(dist=_dist(tmp_path / "dist"), version="0.1.0", source_sha="a" * 40)
    output.write_text(json.dumps(value, indent=2) + "\n")
    with pytest.raises(helper.ReleaseArtifactError, match="not canonical"):
        helper.load_manifest(output)


def test_manifest_rejects_wrong_package(tmp_path: Path, helper) -> None:
    output = tmp_path / "release-manifest.json"
    value = helper.create_manifest(dist=_dist(tmp_path / "dist"), version="0.1.0", source_sha="a" * 40)
    value["package"] = "different-package"
    output.write_bytes(helper._manifest_bytes(value))
    with pytest.raises(helper.ReleaseArtifactError, match="identity"):
        helper.load_manifest(output)


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://code.example",
        "https://user@code.example",
        "https://code.example/api",
        "https://code.example?query=yes",
        "https://code.example#fragment",
    ],
)
def test_registry_origin_rejects_non_origins(helper, origin: str) -> None:
    with pytest.raises(helper.ReleaseArtifactError, match="HTTPS origin"):
        helper.validate_registry_origin(origin)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://registry.example.test", "https://registry.example.test"),
        ("https://registry.example.test/", "https://registry.example.test"),
        ("https://registry.example.test:8443", "https://registry.example.test:8443"),
    ],
)
def test_registry_origin_accepts_origins(helper, value: str, expected: str) -> None:
    assert helper.validate_registry_origin(value) == expected


def _index_json(*, name: str = "netbox-packer", version: str = "0.1.0", urls: object = None) -> bytes:
    if urls is None:
        urls = [{"filename": "netbox_packer-0.1.0.tar.gz", "digests": {"sha256": "b" * 64}}]
    return json.dumps({"info": {"name": name, "version": version}, "urls": urls}).encode()


def test_public_index_good_case_returns_digests(helper) -> None:
    seen: list[str] = []

    def request(url: str, maximum: int) -> bytes:
        assert maximum == 4 * 1024 * 1024
        seen.append(url)
        return _index_json()

    assert helper.check_public_index("0.1.0", request_fn=request) == [
        {"filename": "netbox_packer-0.1.0.tar.gz", "sha256": "b" * 64}
    ]
    assert seen == ["https://pypi.org/pypi/netbox-packer/0.1.0/json"]


def test_public_index_rc_uses_testpypi(helper) -> None:
    def request(url: str, _maximum: int) -> bytes:
        assert url == "https://test.pypi.org/pypi/netbox-packer/0.1.0rc1/json"
        return _index_json(version="0.1.0rc1")

    helper.check_public_index("0.1.0rc1", request_fn=request)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_index_json(name="other"), "name"),
        (_index_json(version="0.1.1"), "version"),
        (_index_json(urls=[]), "no files"),
    ],
)
def test_public_index_rejects_identity_and_empty_files(helper, payload: bytes, message: str) -> None:
    with pytest.raises(helper.ReleaseArtifactError, match=message):
        helper.check_public_index("0.1.0", request_fn=lambda _url, _maximum: payload)


def test_public_index_rejects_404(helper) -> None:
    def request(_url: str, _maximum: int) -> bytes:
        raise helper.ReleaseArtifactError("Public index request failed")

    with pytest.raises(helper.ReleaseArtifactError, match="request failed"):
        helper.check_public_index("0.1.0", request_fn=request)


def test_public_index_rejects_oversize_body(helper) -> None:
    body = b"{" + b" " * (helper.MAX_INDEX_BYTES + 1)
    with pytest.raises(helper.ReleaseArtifactError, match="size bound"):
        helper.check_public_index("0.1.0", request_fn=lambda _url, _maximum: body)


def test_registry_client_refuses_real_redirect(helper) -> None:
    requested: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"target")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        client = helper.RegistryClient(origin, "secret")
        with pytest.raises(helper.ReleaseArtifactError, match="HTTP 302"):
            client.request("GET", "/start")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert requested == ["/start"]


def _link_path(owner: str, package_type: str, package: str, repository: str) -> str:
    return f"/api/v1/packages/{owner}/{package_type}/{package}/-/link/{repository}"


class FakeRegistry:
    origin = "https://registry.example.test"
    owner = "emersonfelipesp"
    repository = "netbox-packer"
    version = "0.1.0"

    def __init__(self) -> None:
        self.packages: dict[tuple[str, str], dict[str, object]] = {}
        self.requests: list[tuple[str, str]] = []
        self.overrides: dict[tuple[str, str], int] = {}

    def add_package(self, package_type: str, name: str, files: dict[str, bytes], *, linked: str | None = None) -> None:
        self.packages[(package_type, name)] = {"files": dict(files), "linked": linked}

    def upload_pypi(self, paths: list[Path]) -> None:
        package = self.packages.setdefault(("pypi", "netbox-packer"), {"files": {}, "linked": None})
        files = package["files"]
        assert isinstance(files, dict)
        for path in paths:
            assert path.name not in files, "registry files are immutable"
            files[path.name] = path.read_bytes()

    def transport(self, request, _maximum: int) -> tuple[int, bytes]:
        method = request.get_method()
        path = urllib.parse.urlsplit(request.full_url).path
        self.requests.append((method, path))
        override = self.overrides.get((method, path))
        if override is not None:
            if method == "POST" and override in {201, 204}:
                self._link(path)
            return override, b""
        if method == "PUT":
            if ("generic", "netbox-packer-release-manifest") in self.packages:
                return 409, b""
            self.add_package("generic", "netbox-packer-release-manifest", {"release-manifest.json": request.data})
            return 201, b""
        if method == "POST":
            if "/-/link/" not in path:
                return 404, b""
            self._link(path)
            return 201, b""
        return self._get(path)

    def _link(self, path: str) -> None:
        parts = path.split("/")
        key = (parts[5], parts[6])
        self.packages[key]["linked"] = f"{self.owner}/{self.repository}"

    def _get(self, path: str) -> tuple[int, bytes]:
        if path.startswith("/api/v1/packages/"):
            return self._get_api(path)
        for (package_type, _name), package in self.packages.items():
            marker = f"/{package_type}/"
            if marker in path:
                filename = path.rsplit("/", 1)[-1]
                files = package["files"]
                assert isinstance(files, dict)
                return (200, files[filename]) if filename in files else (404, b"")
        return 404, b""

    def _get_api(self, path: str) -> tuple[int, bytes]:
        parts = path.split("/")
        key = (parts[5], parts[6])
        package = self.packages.get(key)
        if package is None:
            return 404, b""
        if path.endswith("/files"):
            files = package["files"]
            assert isinstance(files, dict)
            value = [
                {"name": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                for name, content in files.items()
            ]
        else:
            linked = package["linked"]
            value = {
                "name": key[1],
                "version": self.version,
                "type": key[0],
                "repository": None if linked is None else {"full_name": linked},
            }
        return 200, json.dumps(value).encode()


def _publication(tmp_path: Path, helper, registry: FakeRegistry):
    tmp_path.mkdir(parents=True, exist_ok=True)
    dist = _dist(tmp_path / "dist")
    manifest = tmp_path / "release-manifest.json"
    uploads = tmp_path / "upload-list"
    helper.write_manifest(dist=dist, version=registry.version, source_sha="a" * 40, output=manifest)
    arguments = {
        "registry_origin": registry.origin,
        "owner": registry.owner,
        "repository": registry.repository,
        "version": registry.version,
        "source_sha": "a" * 40,
        "dist": dist,
        "manifest_path": manifest,
        "transport": registry.transport,
    }
    helper.plan_publication(**arguments, upload_list=uploads)
    paths = [Path(line) for line in uploads.read_text().splitlines()]
    return arguments, paths


def test_fresh_publication_links_with_201_and_verifies(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    arguments, paths = _publication(tmp_path, helper, registry)
    assert {path.name for path in paths} == set(helper._expected_names("0.1.0"))
    registry.upload_pypi(paths)
    helper.finish_publication(**arguments)
    assert sum(method == "PUT" for method, _path in registry.requests) == 1
    assert sum(method == "POST" for method, _path in registry.requests) == 2
    pypi = helper._package_path(registry.owner, "pypi", "netbox-packer", registry.version)
    assert registry.requests.count(("GET", pypi)) == 2


def test_finish_accepts_link_204(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    arguments, paths = _publication(tmp_path, helper, registry)
    registry.upload_pypi(paths)
    for package_type, package in (("pypi", "netbox-packer"), ("generic", "netbox-packer-release-manifest")):
        registry.overrides[("POST", _link_path(registry.owner, package_type, package, registry.repository))] = 204
    helper.finish_publication(**arguments)


@pytest.mark.parametrize("status", [400, 409])
@pytest.mark.parametrize("linked", ["emersonfelipesp/netbox-packer", "different/repository"])
def test_failed_link_requires_confirmed_association(tmp_path: Path, helper, linked: str, status: int) -> None:
    registry = FakeRegistry()
    arguments, paths = _publication(tmp_path, helper, registry)
    registry.upload_pypi(paths)
    registry.overrides[("POST", _link_path(registry.owner, "pypi", "netbox-packer", registry.repository))] = status
    registry.packages[("pypi", "netbox-packer")]["linked"] = linked
    if linked == "emersonfelipesp/netbox-packer":
        helper.finish_publication(**arguments)
    else:
        with pytest.raises(helper.ReleaseArtifactError, match="different repository"):
            helper.finish_publication(**arguments)


def test_plan_lists_only_missing_sdist_when_identical_wheel_exists(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    wheel = helper._expected_names("0.1.0")[0]
    registry.add_package("pypi", "netbox-packer", {wheel: b"wheel"})
    arguments, paths = _publication(tmp_path, helper, registry)
    assert [path.name for path in paths] == ["netbox_packer-0.1.0.tar.gz"]
    assert (arguments["dist"] / wheel).read_bytes() == b"wheel"


def test_plan_aborts_when_remote_artifact_differs_from_local_build(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    wheel = helper._expected_names("0.1.0")[0]
    registry.add_package("pypi", "netbox-packer", {wheel: b"bytes-ci-never-built"})
    with pytest.raises(helper.ReleaseArtifactError, match="differs from the local build"):
        _publication(tmp_path, helper, registry)
    # The local build output and its manifest stay untouched.
    assert (tmp_path / "dist" / wheel).read_bytes() == b"wheel"
    manifest = helper.load_manifest(tmp_path / "release-manifest.json")
    assert {row["name"]: row["sha256"] for row in manifest["artifacts"]}[wheel] == hashlib.sha256(b"wheel").hexdigest()
    assert not any(method in {"PUT", "POST"} for method, _path in registry.requests)


def test_plan_requires_the_build_step_manifest(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    dist = _dist(tmp_path / "dist")
    manifest = tmp_path / "release-manifest.json"
    helper.write_manifest(dist=dist, version="0.1.0", source_sha="a" * 40, output=manifest)
    (dist / "netbox_packer-0.1.0.tar.gz").write_bytes(b"changed-after-manifest")
    with pytest.raises(helper.ReleaseArtifactError, match="does not match"):
        helper.plan_publication(
            registry_origin=registry.origin,
            owner=registry.owner,
            repository=registry.repository,
            version="0.1.0",
            source_sha="a" * 40,
            dist=dist,
            manifest_path=manifest,
            upload_list=tmp_path / "upload-list",
            transport=registry.transport,
        )
    assert registry.requests == []


@pytest.mark.parametrize("package_type", ["pypi", "generic"])
def test_successful_null_metadata_is_not_absence(tmp_path: Path, helper, package_type: str) -> None:
    registry = FakeRegistry()
    name = "netbox-packer" if package_type == "pypi" else "netbox-packer-release-manifest"
    base = helper._package_path(registry.owner, package_type, name, registry.version)
    original = registry._get

    def null_metadata(path: str):
        return (200, b"null") if path == base else original(path)

    registry._get = null_metadata
    with pytest.raises(helper.ReleaseArtifactError, match="empty JSON"):
        _publication(tmp_path, helper, registry)
    assert not any(method in {"PUT", "POST"} for method, _path in registry.requests)


def test_resume_after_both_artifacts_uploaded(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    dist = _dist(tmp_path / "seed")
    registry.upload_pypi(list(dist.iterdir()))
    arguments, paths = _publication(tmp_path, helper, registry)
    assert paths == []
    helper.finish_publication(**arguments)


def test_resume_after_manifest_uploaded_but_unlinked(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    arguments, paths = _publication(tmp_path, helper, registry)
    registry.upload_pypi(paths)
    raw = arguments["manifest_path"].read_bytes()
    registry.add_package("generic", "netbox-packer-release-manifest", {"release-manifest.json": raw})
    arguments, paths = _publication(tmp_path / "retry", helper, registry)
    assert paths == []
    helper.finish_publication(**arguments)
    assert not any(method == "PUT" for method, _path in registry.requests)


def test_plan_rejects_different_remote_manifest_without_put(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    registry.add_package("generic", "netbox-packer-release-manifest", {"release-manifest.json": b"different"})
    with pytest.raises(helper.ReleaseArtifactError, match="differs"):
        _publication(tmp_path, helper, registry)
    assert not any(method == "PUT" for method, _path in registry.requests)


def test_plan_rejects_unexpected_remote_file(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    registry.add_package("pypi", "netbox-packer", {"extra.whl": b"extra"})
    with pytest.raises(helper.ReleaseArtifactError, match="inventory"):
        _publication(tmp_path, helper, registry)


def test_plan_rejects_remote_digest_mismatch(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    wheel = helper._expected_names("0.1.0")[0]
    registry.add_package("pypi", "netbox-packer", {wheel: b"wheel"})
    original = registry._get_api

    def corrupt(path: str):
        status, body = original(path)
        if path.endswith("/files"):
            value = json.loads(body)
            value[0]["sha256"] = "0" * 64
            body = json.dumps(value).encode()
        return status, body

    registry._get_api = corrupt
    with pytest.raises(helper.ReleaseArtifactError, match="differs"):
        _publication(tmp_path, helper, registry)


@pytest.mark.parametrize("status", [401, 500])
def test_plan_fails_on_non_404_metadata_status(tmp_path: Path, helper, status: int) -> None:
    registry = FakeRegistry()
    base = helper._package_path(registry.owner, "pypi", "netbox-packer", registry.version)
    registry.overrides[("GET", base)] = status
    with pytest.raises(helper.ReleaseArtifactError, match=f"HTTP {status}"):
        _publication(tmp_path, helper, registry)


def test_plan_rejects_pypi_linked_elsewhere(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    registry.add_package("pypi", "netbox-packer", {}, linked="different/repository")
    with pytest.raises(helper.ReleaseArtifactError, match="different repository"):
        _publication(tmp_path, helper, registry)


def test_verification_rejects_extra_pypi_file(tmp_path: Path, helper) -> None:
    registry = FakeRegistry()
    arguments, paths = _publication(tmp_path, helper, registry)
    registry.upload_pypi(paths)
    helper.finish_publication(**arguments)
    files = registry.packages[("pypi", "netbox-packer")]["files"]
    assert isinstance(files, dict)
    files["extra.whl"] = b"extra"
    with pytest.raises(helper.ReleaseArtifactError, match="inventory"):
        helper.finish_publication(**arguments)


def test_workflow_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"tag_name"}
    assert inputs["tag_name"]["required"] == "true"
    job = workflow["jobs"]["publish"]
    assert job["if"] == "${{ github.repository == 'emersonfelipesp/netbox-packer' && github.ref == 'refs/heads/main' }}"
    assert job["runs-on"] == "mirror-host"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "netbox-packer-package-publication",
        "cancel-in-progress": "false",
    }
    assert text.count("secrets.PACKAGE_WRITE_TOKEN") == 2
    assert "secrets.PKG_TOKEN" not in text
    for step in job["steps"]:
        script = step.get("run")
        if script:
            assert "${{" not in script
            result = subprocess.run(
                ["/bin/bash", "-n"], input=script, text=True, capture_output=True, timeout=10, check=False
            )
            assert result.returncode == 0, result.stderr
    publication = job["steps"][-1]["run"]
    public_check = text.index("release_artifacts.py public-index")
    origin = text.index("release_artifacts.py registry-origin")
    plan = text.index("release_artifacts.py plan")
    upload = text.index("python3 -m twine upload")
    finish = text.index("release_artifacts.py finish")
    assert public_check < origin < plan < upload < finish
    assert publication.index("registry-origin") < publication.index("twine upload")
    assert "curl" not in publication
    assert publication.count("GITHUB_SERVER_URL") == 1
    assert "fetch-gitea" not in publication


def test_workflow_uses_pinned_checkout() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2" in text
    assert "persist-credentials: false" in text
    assert "workflow_call" not in text
    assert "push:" not in text


def _flag_values(script: str, flag: str) -> list[str]:
    tokens = script.replace("\\\n", " ").split()
    return [tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token == flag]


def test_workflow_release_layout_keeps_the_manifest_out_of_the_dist_directory(tmp_path: Path, helper) -> None:
    # Replay the build step's transfer layout, then feed the directories the
    # publication step names to the real manifest builder.
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["publish"]["steps"]
    build = next(step["run"] for step in steps if "scripts/release_artifacts.py manifest" in step.get("run", ""))
    publication = steps[-1]["run"]
    layout = [line.strip() for line in build.splitlines() if "release-transfer" in line]
    assert layout, "the build step must stage artifacts for the publication step"
    build_root = tmp_path / "build"
    build_root.mkdir()
    _dist(build_root / "dist")
    (build_root / "release-manifest.json").write_bytes(b"{}\n")
    script = "set -euo pipefail\n" + "\n".join(layout) + "\n"
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "build_root": str(build_root)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    dist_dirs = set(_flag_values(publication, "--dist"))
    manifests = set(_flag_values(publication, "--manifest"))
    assert len(dist_dirs) == 1 and len(manifests) == 1
    dist_dir = tmp_path / dist_dirs.pop()
    manifest = tmp_path / manifests.pop()
    assert manifest.parent != dist_dir
    assert manifest.exists()
    created = helper.create_manifest(dist=dist_dir, version="0.1.0", source_sha="a" * 40)
    assert [row["name"] for row in created["artifacts"]] == [
        "netbox_packer-0.1.0-py3-none-any.whl",
        "netbox_packer-0.1.0.tar.gz",
    ]


def test_manifest_inside_dist_directory_is_refused(tmp_path: Path, helper) -> None:
    dist = _dist(tmp_path / "dist")
    (dist / "release-manifest.json").write_bytes(b"{}\n")
    with pytest.raises(helper.ReleaseArtifactError):
        helper.create_manifest(dist=dist, version="0.1.0", source_sha="a" * 40)


def test_build_step_uses_canonical_helper_and_pinned_timestamps() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    build = next(
        step["run"] for step in workflow["jobs"]["publish"]["steps"] if step.get("name", "").startswith("Build exact")
    )
    assert "python3 scripts/release_artifacts.py manifest" in build
    assert '"$build_root/scripts/release_artifacts.py"' not in build
    assert 'SOURCE_DATE_EPOCH="$(git log -1 --format=%ct "$SOURCE_SHA")"' in build
    assert "export SOURCE_DATE_EPOCH" in build
    assert '--outdir "$build_root/dist" "$build_root/src"' in build
