#!/usr/bin/env python3
"""Reject private-product references in publishable repository files."""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_SIGNATURES = (
    (3, "00e1e10b7f275ff455afed38eb08cb1bfcc007805f50e0d6e8573f503a63f4be", False),
    (14, "4da4c03ebc7d67c400f58be2c1ef091bd5748a87309af2d6e9e8e0aa35431956", True),
    (15, "938733f201571c52d7cb2528abc1603ce5cb392642a0d74e4d958eb2e99042fb", True),
    (18, "eeab65e64fadd11de98f393abf9f6f112755d6f36d691483c47d8f6af2e3590a", True),
    (7, "184ac6413ac89334b3313bd69e5cfb82d1f3f5c0a38865cf426976067fb39ef8", True),
)
_ESCAPED_CODEPOINT = re.compile(r"(?:\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})|%([0-9a-fA-F]{2}))")
_HTML_CODEPOINT = re.compile(r"&#(?:x([0-9a-fA-F]+)|([0-9]+));", re.IGNORECASE)
_HEX_RUN = re.compile(r"[0-9a-f]+", re.IGNORECASE)
_SPLIT_CANDIDATE = re.compile(
    r"(?<![a-z0-9])([a-z0-9])([^a-z0-9]*)([a-z0-9])([^a-z0-9]*)([a-z0-9])(?![a-z0-9])",
    re.IGNORECASE,
)


def _run_git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is unavailable; refusing to skip the boundary scan")
    try:
        result = subprocess.run(
            [git, "-C", str(root), *args],
            input=input_bytes,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        command = " ".join(args)
        raise RuntimeError(f"Git command failed ({command}); refusing to skip the boundary scan") from exc
    return result.stdout


def _decode_escapes(value: str) -> str:
    def escaped_replacement(match: re.Match[str]) -> str:
        encoded = next(group for group in match.groups() if group is not None)
        try:
            return chr(int(encoded, 16))
        except (OverflowError, ValueError):
            return match.group(0)

    def html_replacement(match: re.Match[str]) -> str:
        encoded = match.group(1) or match.group(2)
        base = 16 if match.group(1) else 10
        try:
            return chr(int(encoded, base))
        except (OverflowError, ValueError):
            return match.group(0)

    for _iteration in range(3):
        decoded = _ESCAPED_CODEPOINT.sub(escaped_replacement, value)
        decoded = _HTML_CODEPOINT.sub(html_replacement, decoded)
        decoded = html.unescape(decoded)
        if decoded == value:
            break
        value = decoded
    return decoded


def _matches_forbidden_digest(value: str, forbidden_digest: str, forbidden_length: int) -> bool:
    if len(value) != forbidden_length:
        return False
    try:
        encoded = value.casefold().encode("ascii")
    except UnicodeEncodeError:
        return False
    return hashlib.sha256(encoded).hexdigest() == forbidden_digest


def _contains_forbidden(
    value: bytes,
    *,
    forbidden_digest: str,
    forbidden_length: int,
    compact: bool = False,
) -> bool:
    decoded = value.decode("utf-8", errors="surrogateescape")
    normalized = unicodedata.normalize("NFKC", _decode_escapes(decoded)).casefold()
    if compact:
        normalized = re.sub(r"[^a-z0-9]+", "", normalized)

    if any(
        _matches_forbidden_digest(normalized[index : index + forbidden_length], forbidden_digest, forbidden_length)
        for index in range(max(0, len(normalized) - forbidden_length + 1))
    ):
        return True

    for match in _SPLIT_CANDIDATE.finditer(normalized):
        first, first_separator, second, second_separator, third = match.groups()
        if not first_separator and not second_separator:
            continue
        candidate = first + second + third
        if _matches_forbidden_digest(candidate, forbidden_digest, forbidden_length):
            return True

    encoded_length = forbidden_length * 2
    for match in _HEX_RUN.finditer(normalized):
        run = match.group(0)
        for index in range(max(0, len(run) - encoded_length + 1)):
            try:
                candidate = bytes.fromhex(run[index : index + encoded_length]).decode("ascii")
            except (UnicodeDecodeError, ValueError):
                continue
            if _matches_forbidden_digest(candidate, forbidden_digest, forbidden_length):
                return True
    return False


def find_violations(
    files: Mapping[str, bytes],
    *,
    origin: str = "input",
    forbidden_digest: str | None = None,
    forbidden_length: int = 3,
    compact: bool = False,
) -> list[str]:
    """Return matching paths while retaining malformed-byte and multiline coverage."""

    violations = []
    for name, content in sorted(files.items()):
        path_bytes = os.fsencode(name)
        signatures = (
            ((forbidden_length, forbidden_digest, compact),) if forbidden_digest is not None else _FORBIDDEN_SIGNATURES
        )
        if any(
            _contains_forbidden(path_bytes, forbidden_digest=digest, forbidden_length=length, compact=is_compact)
            for length, digest, is_compact in signatures
        ):
            violations.append(f"{origin}:{name!r}: forbidden reference in path")
        if any(
            _contains_forbidden(content, forbidden_digest=digest, forbidden_length=length, compact=is_compact)
            for length, digest, is_compact in signatures
        ):
            violations.append(f"{origin}:{name!r}: forbidden reference in content")
    return violations


def index_files(root: Path = ROOT) -> dict[str, bytes]:
    """Read regular files and symlink targets from the exact Git index blobs."""

    entries = _run_git(root, "ls-files", "--stage", "-z").split(b"\0")
    files = {}
    for raw_entry in filter(None, entries):
        try:
            metadata, raw_name = raw_entry.split(b"\t", 1)
            mode, object_id, stage = metadata.split()
        except ValueError as exc:
            raise RuntimeError("Git returned an invalid index entry; refusing to continue") from exc
        if stage != b"0":
            raise RuntimeError(f"Unmerged index entry is unsupported: {os.fsdecode(raw_name)!r}")
        if mode not in {b"100644", b"100755", b"120000"}:
            raise RuntimeError(f"Unsupported publishable index mode {mode.decode()}: {os.fsdecode(raw_name)!r}")
        name = os.fsdecode(raw_name)
        files[name] = _run_git(root, "cat-file", "blob", object_id.decode("ascii"))
    if not files:
        raise RuntimeError("Git returned no indexed publishable files; refusing to pass an empty scan")
    return files


def worktree_files(root: Path = ROOT) -> dict[str, bytes]:
    """Read tracked and unignored files from the working tree without following symlinks."""

    names = _run_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    files = {}
    for raw_name in filter(None, names):
        name = os.fsdecode(raw_name)
        path = root / name
        if not os.path.lexists(path):
            continue
        if path.is_symlink():
            files[name] = os.fsencode(os.readlink(path))
            continue
        if not path.is_file():
            raise RuntimeError(f"Unsupported publishable working-tree path: {name!r}")
        files[name] = path.read_bytes()
    if not files:
        raise RuntimeError("No readable working-tree files remain; refusing to pass an empty scan")
    return files


def repository_violations(root: Path = ROOT, *, source: str = "all") -> list[str]:
    violations = []
    if source in {"all", "index"}:
        violations.extend(find_violations(index_files(root), origin="index"))
    if source in {"all", "worktree"}:
        violations.extend(find_violations(worktree_files(root), origin="worktree"))
    return violations


def run_mutation_tests() -> None:
    """Prove representative content and path evasions are rejected."""

    token = b"qvz"
    digest = hashlib.sha256(token).hexdigest()
    mutations = {
        "embedded": b"prefix" + token.upper() + b"suffix",
        "split literal": token[:1] + b'" + "' + token[1:],
        "multiline": token[:1] + b"\n" + token[1:],
        "hyphenated": token[:1] + b"-" + token[1:],
        "long split": token[:1] + (b"-" * 64) + token[1:],
        "hex escaped": b"".join(f"\\x{value:02x}".encode() for value in token),
        "unicode escaped": b"".join(f"\\u{value:04x}".encode() for value in token),
        "percent escaped": b"".join(f"%{value:02x}".encode() for value in token),
        "plain hex": token.hex().encode(),
        "html escaped": b"".join(f"&#{value};".encode() for value in token),
        "fullwidth unicode": "".join(chr(value + 0xFEE0) for value in token.upper()).encode(),
        "invalid bytes": b"\xff" + token + b"\xfe",
        "NUL split": token[:1] + b"\0" + token[1:],
    }

    def scan(files):
        return find_violations(files, forbidden_digest=digest, forbidden_length=len(token))

    if scan({"README.md": b"Standalone machine-image builds."}):
        raise RuntimeError("The clean mutation-test corpus was rejected")
    for name, mutation in mutations.items():
        if not scan({"mutation.bin": mutation}):
            raise RuntimeError(f"Boundary mutation was accepted: {name}")
    split_path = f"private-{chr(token[0])}-{token[1:].decode()}"
    if not scan({split_path: b"clean content"}):
        raise RuntimeError("Boundary mutation was accepted: split path")

    long_token = b"qvzabcde"
    long_digest = hashlib.sha256(long_token).hexdigest()
    if not find_violations(
        {"mutation.txt": long_token[:1] + b"-" + long_token[1:]},
        forbidden_digest=long_digest,
        forbidden_length=len(long_token),
        compact=True,
    ):
        raise RuntimeError("Boundary mutation was accepted: long split signature")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source", choices=("all", "index", "worktree"), default="all")
    args = parser.parse_args()
    if args.self_test:
        run_mutation_tests()
        print("Public-boundary mutation tests passed")
        return
    violations = repository_violations(source=args.source)
    if violations:
        raise SystemExit("Private-product references found:\n" + "\n".join(violations))
    print(f"Public boundary is clean ({args.source})")


if __name__ == "__main__":
    main()
