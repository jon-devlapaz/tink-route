"""Self-update from verified GitHub Release assets, mirroring `tink update`."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_RELEASES_API = "https://api.github.com/repos/jon-devlapaz/tink-route/releases/latest"
RELEASES_API_ENV_VAR = "TINK_ROUTE_RELEASES_API"
PACKAGE_NAME = "tink-route"
PACKAGE_IMPORT = "tink_route"
METADATA_TIMEOUT = 30
ASSET_TIMEOUT = 300


class UpdateError(Exception):
    """Fatal self-update failure; callers translate to exit code 2."""


def _fail(message: str) -> UpdateError:
    return UpdateError(message)


def parse_version(value: str) -> tuple[tuple[int, ...], tuple[Any, ...]]:
    """Split a semantic version into numeric core and prerelease parts."""
    clean = value.strip().lstrip("v")
    core, sep, pre = clean.partition("-")
    parts = core.split(".")
    if not core or len(parts) != 3 or any(not p.isdigit() for p in parts):
        raise _fail(f"invalid semantic version: {value}")
    nums = tuple(int(p) for p in parts)
    pre_parts: tuple[Any, ...] = ()
    if sep:
        if not pre:
            raise _fail(f"invalid semantic version: {value}")
        pre_parts = tuple(int(p) if p.isdigit() else p for p in pre.split("."))
    return nums, pre_parts


def compare_versions(left: str, right: str) -> int:
    """Return -1/0/1. A release outranks its prereleases on equal core."""
    l_nums, l_pre = parse_version(left)
    r_nums, r_pre = parse_version(right)
    width = max(len(l_nums), len(r_nums))
    l_nums += (0,) * (width - len(l_nums))
    r_nums += (0,) * (width - len(r_nums))
    if l_nums != r_nums:
        return -1 if l_nums < r_nums else 1
    if l_pre == r_pre:
        return 0
    if not l_pre:
        return 1
    if not r_pre:
        return -1
    return -1 if l_pre < r_pre else 1


def validate_release_url(url: str, allow_file: bool = False) -> str:
    """Accept https URLs without credentials, query, or fragment; optionally file:///..."""
    if not url or any(c in url for c in ("?", "#", "\\")) or any(
        c.isspace() or ord(c) < 32 for c in url
    ):
        raise _fail("release download URL is not allowed")
    if url.startswith("https://"):
        authority = url[len("https://"):].split("/", 1)[0]
        if not authority or "@" in authority:
            raise _fail("release download URL is not allowed")
        return url
    if allow_file and url.startswith("file:///"):
        return url
    raise _fail("release download URL is not allowed")


def releases_api_url() -> tuple[str, bool]:
    """Resolve the releases endpoint and whether file:// assets are permitted."""
    import os

    override = os.environ.get(RELEASES_API_ENV_VAR)
    if override:
        return validate_release_url(override, allow_file=True), True
    return DEFAULT_RELEASES_API, False


def parse_sha256(value: str) -> bytes:
    """Parse a sha256:<64-hex> digest."""
    algorithm, sep, hexpart = value.partition(":")
    if not sep or algorithm.lower() != "sha256":
        raise _fail("release asset digest must use sha256:<hex>")
    if len(hexpart) != 64:
        raise _fail("release asset SHA-256 digest must be 64 hexadecimal characters")
    try:
        return bytes.fromhex(hexpart)
    except ValueError as exc:
        raise _fail("release asset has invalid SHA-256 digest") from exc


@dataclass
class ReleaseAsset:
    tag: str
    version: str
    name: str
    url: str
    sha256: bytes | None


def release_version(metadata: dict[str, Any]) -> tuple[str, str]:
    """Extract (tag, version) from release metadata without requiring assets."""
    tag = metadata.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise _fail("release metadata missing tag_name")
    version = tag[1:] if tag.startswith("v") else tag
    parse_version(version)
    return tag, version


def select_update_asset(metadata: dict[str, Any], allow_file: bool) -> ReleaseAsset:
    """Pick the wheel asset, falling back to the sdist, for this release."""
    tag, version = release_version(metadata)
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise _fail("release metadata missing assets")
    by_name: dict[str, dict[str, Any]] = {}
    for a in assets:
        if isinstance(a, dict) and isinstance(a.get("name"), str):
            by_name[str(a["name"])] = a
    want_wheel = f"tink_route-{version}-py3-none-any.whl"
    want_sdist = f"tink-route-{version}.tar.gz"
    match = by_name.get(want_wheel) or by_name.get(want_sdist)
    if match is None:
        have = ", ".join(sorted(by_name)) or "(none)"
        raise _fail(f"no installable release asset (want {want_wheel} or {want_sdist}; have: {have})")
    url = match.get("browser_download_url")
    if not isinstance(url, str):
        raise _fail(f"asset {match.get('name')} missing browser_download_url")
    validate_release_url(url, allow_file=allow_file)
    digest_raw = match.get("digest")
    digest: bytes | None = None
    if isinstance(digest_raw, str) and digest_raw:
        digest = parse_sha256(digest_raw)
    return ReleaseAsset(
        tag=tag,
        version=version,
        name=str(match.get("name")),
        url=url,
        sha256=digest,
    )


def fetch_json(url: str, timeout: int) -> dict[str, Any]:
    """GET a JSON document over https (or file:// when explicitly allowed)."""
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tink-route/update"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except UpdateError:
        raise
    except Exception as exc:
        raise _fail(f"could not fetch release metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise _fail("release metadata was not a JSON object")
    return payload


def download_asset(url: str, dest: Path) -> None:
    """Stream a release asset to dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "tink-route/update"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=ASSET_TIMEOUT) as resp, open(dest, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:
        raise _fail(f"could not download release asset: {exc}") from exc


def verify_digest(path: Path, expected: bytes) -> None:
    """Fail when the file SHA-256 does not match the release digest."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise _fail(f"could not read downloaded asset: {exc}") from exc
    if digest.digest() != expected:
        raise _fail("release archive SHA-256 digest mismatch")


def pip_install(archive: Path) -> None:
    """Install the downloaded distribution with the running interpreter's pip."""
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", str(archive)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        raise _fail(f"pip install failed: {' '.join(tail)}")


def probe_installed_version() -> str:
    """Read the installed version from a fresh interpreter, avoiding import caches."""
    proc = subprocess.run(
        [sys.executable, "-c", "import importlib.metadata; print(importlib.metadata.version('tink-route'))"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise _fail("could not probe installed tink-route version after update")
    return proc.stdout.strip()


def installed_version() -> str:
    """Best-effort current version without importing the package under test."""
    try:
        import importlib.metadata

        return importlib.metadata.version(PACKAGE_NAME)
    except Exception:
        import tink_route

        return tink_route.__version__


@dataclass
class UpdateCheck:
    current: str
    latest: str
    asset: ReleaseAsset | None

    @property
    def newer_available(self) -> bool:
        return self.asset is not None and compare_versions(self.asset.version, self.current) > 0


def check_for_update(api_url: str | None = None) -> UpdateCheck:
    """Compare the installed version against the latest GitHub release."""
    current = installed_version()
    url, allow_file = (api_url, True) if api_url else releases_api_url()
    metadata = fetch_json(url, METADATA_TIMEOUT)
    tag, release_ver = release_version(metadata)
    relation = compare_versions(release_ver, current)
    if relation < 0:
        raise _fail(f"refusing to downgrade tink-route from v{current} to v{release_ver}")
    if relation == 0:
        return UpdateCheck(current=current, latest=release_ver, asset=None)
    asset = select_update_asset(metadata, allow_file)
    return UpdateCheck(current=current, latest=asset.version, asset=asset)


def perform_update(check: UpdateCheck, download: Any = None) -> UpdateCheck:
    """Download, verify, install, and probe the release from a prior check."""
    if check.asset is None or not check.newer_available:
        return check
    fetch = download or download_asset
    with tempfile.TemporaryDirectory(prefix="tink-route-update-") as tmpdir:
        archive = Path(tmpdir) / check.asset.name
        fetch(check.asset.url, archive)
        if check.asset.sha256 is not None:
            verify_digest(archive, check.asset.sha256)
        pip_install(archive)
    probed = probe_installed_version()
    if compare_versions(probed, check.asset.version) != 0:
        raise _fail(f"post-install version probe mismatch: expected v{check.asset.version}, got v{probed}")
    return UpdateCheck(current=probed, latest=check.latest, asset=None)
