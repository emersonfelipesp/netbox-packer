"""Run the PBS installer from the seed against stubbed system commands."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SEED = ROOT / "netbox_packer/seeds/pbs-4-debian-13.vendor-data.yaml"
KEY_BYTES = b"fake archive keyring"

FAKE_CURL = """#!/usr/bin/env bash
output=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then output="$2"; shift; fi
  shift
done
printf '%s' "${FAKE_KEY_CONTENT}" > "${output}"
"""

FAKE_APT_GET = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${FAKE_ROOT}/apt.log"
sources="${FAKE_ROOT}/sources"
case " $* " in
  *" update "*)
    for name in pbs-enterprise.sources pbs-enterprise.list; do
      if [ -e "${sources}/${name}" ]; then
        echo 'E: subscription required' >&2
        exit 100
      fi
    done
    if [ "${FAKE_TRANSIENT_FETCH_FAILURE:-}" = 1 ]; then
      case " $* " in *" --error-on=any "*) exit 100 ;; esac
    fi
    ;;
  *" install "*)
    printf 'Types: deb\\nURIs: https://enterprise.proxmox.com/debian/pbs\\n' \\
      > "${sources}/pbs-enterprise.sources"
    if [ "${FAKE_INSTALL_FAILS:-}" = 1 ]; then exit 100; fi
    ;;
esac
exit 0
"""

FAKE_SS = """#!/usr/bin/env bash
echo 'LISTEN 0 4096 0.0.0.0:8007 0.0.0.0:*'
"""

NOOP = "#!/usr/bin/env bash\nexit 0\n"


def _script(root: Path, key: bytes) -> Path:
    document = yaml.safe_load(SEED.read_text())
    text = document["write_files"][0]["content"]
    sha = hashlib.sha256(key).hexdigest()
    replacements = {
        "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45": sha,
        "/etc/apt/sources.list.d": f"{root}/sources",
        "/usr/share/keyrings": f"{root}/keyrings",
        "/var/log": f"{root}/log",
        "install -o root -g root -m 0644": "install -m 0644",
    }
    for old, new in replacements.items():
        assert old in text
        text = text.replace(old, new)
    path = root / "installer.sh"
    path.write_text(text)
    return path


def _run(
    tmp_path: Path, existing: dict[str, str] | None = None, **env: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path
    for directory in ("bin", "sources", "keyrings", "log"):
        (root / directory).mkdir(exist_ok=True)
    for name, body in (existing or {}).items():
        (root / "sources" / name).write_text(body)
    for name, body in (
        ("curl", FAKE_CURL),
        ("apt-get", FAKE_APT_GET),
        ("ss", FAKE_SS),
        ("systemctl", NOOP),
    ):
        stub = root / "bin" / name
        stub.write_text(body)
        stub.chmod(0o755)
    script = _script(root, KEY_BYTES)
    environment = {
        "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        "FAKE_ROOT": str(root),
        "FAKE_KEY_CONTENT": env.pop("key", KEY_BYTES.decode()),
        **env,
    }
    result = subprocess.run(["bash", str(script)], env=environment, capture_output=True, text=True, timeout=60)
    return result, root


def _marker(root: Path) -> bool:
    log = root / "log/netbox-pbs-install.log"
    return log.exists() and "NETBOX_PBS_INSTALL_COMPLETE" in log.read_text()


def _updates(root: Path) -> list[str]:
    return [line for line in (root / "apt.log").read_text().splitlines() if " update" in line]


def test_install_disables_the_enterprise_repository_and_verifies_updates(tmp_path) -> None:
    result, root = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (root / "sources/pbs-enterprise.sources").exists()
    assert (root / "sources/pbs-enterprise.sources.disabled").exists()
    assert _marker(root)
    assert len(_updates(root)) == 2
    assert all("--error-on=any" in line for line in _updates(root))


def test_transient_repository_failure_does_not_record_completion(tmp_path) -> None:
    result, root = _run(tmp_path, FAKE_TRANSIENT_FETCH_FAILURE="1")
    assert result.returncode != 0
    assert not _marker(root)


def test_failed_install_exits_with_its_status_and_a_retry_recovers(tmp_path) -> None:
    result, root = _run(tmp_path, FAKE_INSTALL_FAILS="1")
    assert result.returncode == 100
    assert not _marker(root)
    # The failed package transaction left the enterprise source behind; the
    # installer must have moved it aside so that the next run can update.
    assert not (root / "sources/pbs-enterprise.sources").exists()
    assert (root / "sources/pbs-enterprise.sources.disabled").exists()
    retry, root = _run(tmp_path)
    assert retry.returncode == 0, retry.stderr
    assert _marker(root)


def test_key_pin_mismatch_fails_before_any_repository_is_configured(tmp_path) -> None:
    result, root = _run(tmp_path, key="tampered keyring")
    assert result.returncode != 0
    assert not (root / "sources/pbs-no-subscription.sources").exists()
    assert not (root / "keyrings/proxmox-archive-keyring.gpg").exists()
    assert not _marker(root)


ENTERPRISE_VARIANTS = {
    "lowercase-field": ("pbs-enterprise.sources", "Types: deb\nenabled: yes\nURIs: https://x\n"),
    "two-stanzas": (
        "pbs-enterprise.sources",
        "Types: deb\nURIs: https://x\n\nTypes: deb\nURIs: https://y\nEnabled: yes\n",
    ),
    "no-types-line": ("pbs-enterprise.sources", "URIs: https://x\nSuites: trixie\n"),
    "tab-separated-list": ("pbs-enterprise.list", "deb\thttps://x\ttrixie\tpbs-enterprise\n"),
}


@pytest.mark.parametrize("name", sorted(ENTERPRISE_VARIANTS))
def test_rerun_recovers_from_any_leftover_enterprise_source(tmp_path, name) -> None:
    filename, body = ENTERPRISE_VARIANTS[name]
    result, root = _run(tmp_path, existing={filename: body})
    assert result.returncode == 0, result.stderr
    assert not (root / "sources" / filename).exists()
    assert (root / "sources" / f"{filename}.disabled").exists()
    assert len(_updates(root)) == 2
    assert _marker(root)
