"""Tests for self-update from GitHub Releases (mirrors `tink update`)."""

import hashlib
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tink_route.adapters import updater
from tink_route.adapters.updater import (
    UpdateCheck,
    UpdateError,
    compare_versions,
    parse_sha256,
    parse_version,
    select_update_asset,
    validate_release_url,
    verify_digest,
)

GOOD_DIGEST = "sha256:" + "ab" * 32


def _meta(tag="v0.6.0", names=("tink_route-0.6.0-py3-none-any.whl",)):
    return {
        "tag_name": tag,
        "assets": [
            {
                "name": n,
                "browser_download_url": f"https://example.test/{n}",
                "digest": GOOD_DIGEST,
            }
            for n in names
        ],
    }


class TestVersions(unittest.TestCase):
    def test_numeric_and_prerelease_order(self) -> None:
        self.assertEqual(compare_versions("0.5.2", "0.5.2"), 0)
        self.assertEqual(compare_versions("0.5.10", "0.5.9"), 1)
        self.assertEqual(compare_versions("v0.5.2", "0.6.0"), -1)
        self.assertEqual(compare_versions("1.0.0-rc.1", "1.0.0"), -1)
        self.assertEqual(compare_versions("1.0.0", "1.0.0-rc.1"), 1)

    def test_invalid_versions_rejected(self) -> None:
        for bad in ("1.2", "01x", "1.2.3-", "v", ""):
            with self.assertRaises(UpdateError, msg=bad):
                parse_version(bad)


class TestUrlsAndDigests(unittest.TestCase):
    def test_url_policy(self) -> None:
        validate_release_url("https://example.test/x.tgz")
        for bad in (
            "https://user@example.test/x.tgz",
            "https://example.test/x.tgz?token=s",
            "https://example.test/x.tgz#frag",
            "http://example.test/x.tgz",
            "file:///tmp/x.tgz",
            "",
        ):
            with self.assertRaises(UpdateError, msg=bad):
                validate_release_url(bad)
        validate_release_url("file:///tmp/x.tgz", allow_file=True)

    def test_digest_policy(self) -> None:
        self.assertEqual(len(parse_sha256(GOOD_DIGEST)), 32)
        for bad in ("md5:" + "ab" * 32, "sha256:xyz", "sha256:" + "ab" * 31, "nope"):
            with self.assertRaises(UpdateError, msg=bad):
                parse_sha256(bad)


class TestAssetSelection(unittest.TestCase):
    def test_wheel_preferred_over_sdist(self) -> None:
        asset = select_update_asset(
            _meta(names=("tink-route-0.6.0.tar.gz", "tink_route-0.6.0-py3-none-any.whl")), False
        )
        self.assertTrue(asset.name.endswith(".whl"))

    def test_sdist_fallback(self) -> None:
        asset = select_update_asset(_meta(names=("tink-route-0.6.0.tar.gz",)), False)
        self.assertTrue(asset.name.endswith(".tar.gz"))

    def test_missing_asset_lists_names(self) -> None:
        with self.assertRaises(UpdateError) as ctx:
            select_update_asset(_meta(names=("other.txt",)), False)
        self.assertIn("other.txt", str(ctx.exception))

    def test_missing_digest_rejected(self) -> None:
        meta = _meta()
        del meta["assets"][0]["digest"]
        with self.assertRaises(UpdateError):
            select_update_asset(meta, False)


class TestCheckFlow(unittest.TestCase):
    def _check(self, current: str) -> UpdateCheck:
        with patch.object(updater, "installed_version", return_value=current), \
             patch.object(updater, "fetch_json", return_value=_meta()):
            from tink_route.adapters.updater import check_for_update
            return check_for_update()

    def test_up_to_date(self) -> None:
        self.assertFalse(self._check("0.6.0").newer_available)

    def test_newer_available(self) -> None:
        check = self._check("0.5.2")
        self.assertTrue(check.newer_available)
        self.assertEqual(check.latest, "0.6.0")

    def test_downgrade_refused(self) -> None:
        with self.assertRaises(UpdateError):
            self._check("9.9.9")


class TestPerformUpdate(unittest.TestCase):
    def _check(self) -> UpdateCheck:
        asset = select_update_asset(_meta(), False)
        return UpdateCheck(current="0.5.2", latest="0.6.0", asset=asset)

    def test_digest_mismatch_aborts(self) -> None:
        def bad_fetch(url: str, dest: Path) -> None:
            dest.write_bytes(b"tampered bytes")
        with self.assertRaises(UpdateError):
            updater.perform_update(self._check(), download=bad_fetch)

    def test_success_installs_and_probes(self) -> None:
        content = b"fake wheel bytes"
        asset = updater.ReleaseAsset(
            tag="v0.6.0", version="0.6.0", name="w.whl",
            url="https://example.test/w.whl", sha256=hashlib.sha256(content).digest(),
        )
        check = UpdateCheck(current="0.5.2", latest="0.6.0", asset=asset)
        seen: dict = {}

        def good_fetch(url: str, dest: Path) -> None:
            seen["url"] = url
            dest.write_bytes(content)

        with patch.object(updater, "pip_install") as pip, \
             patch.object(updater, "probe_installed_version", return_value="0.6.0"):
            done = updater.perform_update(check, download=good_fetch)
        self.assertEqual(done.current, "0.6.0")
        self.assertIsNone(done.asset)
        pip.assert_called_once()
        self.assertEqual(seen["url"], "https://example.test/w.whl")

    def test_probe_mismatch_rejected(self) -> None:
        content = b"fake wheel bytes"
        asset = updater.ReleaseAsset(
            tag="v0.6.0", version="0.6.0", name="w.whl",
            url="https://example.test/w.whl", sha256=hashlib.sha256(content).digest(),
        )
        check = UpdateCheck(current="0.5.2", latest="0.6.0", asset=asset)
        with patch.object(updater, "pip_install"), \
             patch.object(updater, "probe_installed_version", return_value="0.5.2"):
            with self.assertRaises(UpdateError):
                updater.perform_update(check, download=lambda u, d: d.write_bytes(content))

    def test_verify_digest_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.bin"
            p.write_bytes(b"data")
            verify_digest(p, hashlib.sha256(b"data").digest())
            with self.assertRaises(UpdateError):
                verify_digest(p, hashlib.sha256(b"other").digest())


class TestUpdateCli(unittest.TestCase):
    def _main(self, argv: list, check: UpdateCheck) -> tuple:
        from tink_route.cli import main
        buf = io.StringIO()
        with patch.object(sys, "argv", argv), \
             patch("tink_route.adapters.updater.check_for_update", return_value=check), \
             redirect_stdout(buf):
            return main(), buf.getvalue()

    def test_check_reports_available_with_exit_1(self) -> None:
        asset = select_update_asset(_meta(), False)
        code, out = self._main(
            ["tink-route", "update", "--check"],
            UpdateCheck(current="0.5.2", latest="0.6.0", asset=asset),
        )
        self.assertEqual(code, 1)
        self.assertIn("0.6.0", out)

    def test_check_up_to_date_exit_0(self) -> None:
        code, out = self._main(
            ["tink-route", "update", "--check"],
            UpdateCheck(current="0.6.0", latest="0.6.0", asset=None),
        )
        self.assertEqual(code, 0)
        self.assertIn("Up to date", out)


if __name__ == "__main__":
    unittest.main()
