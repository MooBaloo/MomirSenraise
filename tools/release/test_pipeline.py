import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pipeline as p

SOURCE = "a" * 40
CERT = "b" * 64


class ReleaseGuards(unittest.TestCase):
    def meta(self, channel="dev", code=201):
        return p.identity(channel, SOURCE, "0.1.0", code)

    def test_codes_order_runs_and_attempts_without_sha(self):
        self.assertLess(p.allocate(4, 1), p.allocate(4, 2))
        self.assertLess(p.allocate(4, 99), p.allocate(5, 1))
        for run, attempt in [(0, 1), (1, 0), (1, 100), (21_000_000, 1)]:
            with self.assertRaises(ValueError):
                p.allocate(run, attempt)

    def test_shipped_files_and_docs_only(self):
        for path in ["app/src/main/res/values/strings.xml", "app/src/main/jni/x.c",
                     "app/src/main/java/App.kt", "app/build.gradle.kts", "gradle/libs.versions.toml",
                     "release/version.txt"]:
            self.assertTrue(p.shipped(path), path)
        for path in ["README.md", "docs/signing.md", "app/src/test/X.kt", ".github/workflows/publish.yml"]:
            self.assertFalse(p.shipped(path), path)

    def test_channel_names_are_not_launcher_build_labels(self):
        stable, dev = self.meta("stable"), self.meta()
        self.assertEqual(stable["label"], "Momir")
        self.assertEqual(dev["label"], "Momir Dev")
        self.assertNotEqual(stable["package"], dev["package"])
        self.assertEqual(stable["versionName"], "0.1.0")
        self.assertIn("201", dev["versionName"])
        self.assertEqual(dev["source"], SOURCE)

    def test_invalid_inputs_fail_closed(self):
        for args in [("x", SOURCE, "0.1.0", 1), ("dev", "main", "0.1.0", 1),
                     ("dev", SOURCE, "../x", 1), ("dev", SOURCE, "0.1.0", 0)]:
            with self.assertRaises(ValueError):
                p.identity(*args)

    def test_stale_retries_and_existing_tags_are_rejected(self):
        meta = self.meta()
        old = dict(tag_name="dev-999-old", draft=False,
                   body=p.release_body(dict(self.meta(code=999), apkSha256="0"*64, certificateSha256=CERT)))
        with self.assertRaisesRegex(ValueError, "stale"):
            p.check_new(meta, [old])
        with self.assertRaisesRegex(ValueError, "already exists"):
            p.check_new(meta, [dict(tag_name=meta["tag"])])
        with self.assertRaisesRegex(ValueError, "receipt"):
            p.check_new(meta, [dict(tag_name="dev-10-old", draft=False, body="")])

    def test_artifact_bound_to_channel_source_and_run(self):
        meta = self.meta()
        with patch.dict(os.environ, EXPECTED_CHANNEL="dev", EXPECTED_SOURCE=SOURCE, EXPECTED_CODE="201"):
            p.verify_receipt(meta)
            for key, value in [("channel", "stable"), ("source", "c"*40),
                               ("versionCode", 202), ("package", "other.app"), ("tag", "v9.0.0")]:
                with self.assertRaises(ValueError):
                    p.verify_receipt(dict(meta, **{key: value}))

    def test_current_branch_requires_protection_and_exact_head(self):
        for state in [dict(protected=False, commit=dict(sha=SOURCE)),
                      dict(protected=True, commit=dict(sha="c"*40))]:
            with patch.object(p, "api", return_value=state), self.assertRaises(ValueError):
                p.current("dev", SOURCE)

    def test_apk_metadata_and_embedded_source(self):
        meta = self.meta()
        badging = (f"package: name='{meta['package']}' versionCode='201' versionName='{meta['versionName']}'\n"
                   "application-label:'Momir Dev'\n")
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp)/"app.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("assets/build-identity.json", json.dumps({k:meta[k] for k in
                                 ["source", "channel", "versionCode", "versionName"]}))
            with patch.object(p, "command", return_value=badging):
                p.verify_apk(apk, meta, "aapt")
                with self.assertRaises(ValueError):
                    p.verify_apk(apk, dict(meta, source="c"*40), "aapt")
            for output in [badging + "application-debuggable", badging.replace("Momir Dev", "Momir Dev 201"),
                           badging.replace("versionCode='201'", "versionCode='202'")]:
                with patch.object(p, "command", return_value=output), self.assertRaises(ValueError):
                    p.verify_apk(apk, meta, "aapt")

    def test_docs_push_skips_before_any_remote_or_secret_access(self):
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp)/"event.json"
            event.write_text(json.dumps(dict(after=SOURCE, before="c"*40)))
            out = Path(temp)/"output"
            env = dict(GITHUB_REPOSITORY=p.REPOSITORY, GITHUB_REF="refs/heads/main", GITHUB_EVENT_PATH=str(event))
            with patch.dict(os.environ, env), patch.object(p, "command", side_effect=[SOURCE, "README.md\n"]), \
                 patch.object(p.subprocess, "run"), patch.object(p, "api") as remote:
                p.plan(out)
                self.assertEqual(out.read_text(), "enabled=false\n")
                remote.assert_not_called()

    def test_stable_direct_push_cannot_sign(self):
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp)/"event.json"
            event.write_text(json.dumps(dict(after=SOURCE, before="c"*40)))
            env = dict(GITHUB_REPOSITORY=p.REPOSITORY, GITHUB_REF="refs/heads/main", GITHUB_EVENT_PATH=str(event))
            with patch.dict(os.environ, env), patch.object(p, "command", side_effect=[SOURCE, "app/src/main/App.kt", ""]), \
                 patch.object(p.subprocess, "run"), patch.object(p, "current"), \
                 patch.object(p, "api", side_effect=[dict(commit=dict(sha=SOURCE)), []]):
                with self.assertRaisesRegex(ValueError, "promotion"):
                    p.plan(Path(temp)/"output")

    def test_dev_workflow_difference_stops_without_broader_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp)/"event.json"
            event.write_text(json.dumps(dict(after=SOURCE, before="c"*40)))
            env = dict(GITHUB_REPOSITORY=p.REPOSITORY, GITHUB_REF="refs/heads/dev", GITHUB_EVENT_PATH=str(event))
            with patch.dict(os.environ, env), patch.object(p, "command", side_effect=[SOURCE, "app/src/main/App.kt", ".github/workflows/build.yml"]), \
                 patch.object(p.subprocess, "run"), patch.object(p, "current"), \
                 patch.object(p, "api", return_value=dict(commit=dict(sha=SOURCE))):
                with self.assertRaisesRegex(ValueError, "workflow changes"):
                    p.plan(Path(temp)/"output")

    def test_certificate_verification_is_exact(self):
        with patch.object(p, "command", return_value=f"Signer #1 certificate SHA-256 digest: {CERT}"):
            p.verify_signature("a.apk", CERT, "apksigner")
            with self.assertRaises(ValueError):
                p.verify_signature("a.apk", "c"*64, "apksigner")
        with patch.object(p, "command", return_value=""), self.assertRaises(ValueError):
            p.verify_signature("a.apk", CERT, "apksigner")

    def test_same_channel_keys_rejected_before_key_access(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/"identity.json").write_text(json.dumps(self.meta()))
            with patch.dict(os.environ, EXPECTED_CHANNEL="dev", EXPECTED_SOURCE=SOURCE,
                            EXPECTED_CODE="201", EXPECTED_CERT_SHA256=CERT, OTHER_CHANNEL_CERT_SHA256=CERT):
                with self.assertRaisesRegex(ValueError, "different keys"):
                    p.sign(temp)

    def publication(self, fail_asset=False, immutable=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        (directory/"momir.apk").write_bytes(b"test fixture, not a signed APK")
        meta = dict(self.meta(), certificateSha256=CERT,
                    apkSha256=hashlib.sha256((directory/"momir.apk").read_bytes()).hexdigest())
        (directory/"identity.json").write_text(json.dumps(meta))
        (directory/"momir.apk.sha256").write_text(meta["apkSha256"] + "  momir.apk\n")
        calls = []
        old = dict(id=2, tag_name="development", draft=False, prerelease=True, immutable=True,
                   body=p.release_body(dict(meta, versionCode=101)))

        def api(path, method="GET", data=None):
            calls.append((path, method, data))
            if path == "releases" and method == "POST":
                return dict(id=1)
            if path == "releases/1" and method == "GET":
                assets = [dict(name=n, state="uploaded", digest="sha256:" +
                               hashlib.sha256((directory/n).read_bytes()).hexdigest())
                          for n in ["momir.apk", "momir.apk.sha256", "identity.json"]]
                if fail_asset:
                    assets[0]["digest"] = "sha256:" + "0"*64
                return dict(assets=assets)
            if path == "releases/1" and method == "PATCH":
                return dict(immutable=immutable, html_url=f"https://github.com/{p.REPOSITORY}/releases/tag/{meta['tag']}")
            if path == "releases/2" and method == "PATCH":
                return {}
            raise AssertionError((path, method, data))

        env = dict(EXPECTED_CHANNEL="dev", EXPECTED_SOURCE=SOURCE, EXPECTED_CODE="201",
                   EXPECTED_CERT_SHA256=CERT, ANDROID_HOME="/sdk")
        with patch.dict(os.environ, env), patch.object(p, "api", side_effect=api), \
             patch.object(p, "releases", return_value=[old]), patch.object(p, "current"), \
             patch.object(p, "verify_apk"), patch.object(p, "verify_signature"), patch.object(p.subprocess, "run"):
            if fail_asset or not immutable:
                with self.assertRaises(ValueError):
                    p.publish(directory)
            else:
                p.publish(directory)
        return calls

    def test_pointer_updates_only_after_verified_complete_publication(self):
        calls = self.publication()
        self.assertEqual(calls[-1][:2], ("releases/2", "PATCH"))
        self.assertEqual(set(calls[-1][2]), {"body"})  # Never moves/replaces tag or assets.
        self.assertEqual(calls[-2][2]["draft"], False)
        self.assertIn("Download APK", calls[-1][2]["body"])

    def test_failed_asset_upload_preserves_previous_public_preview(self):
        calls = self.publication(fail_asset=True)
        self.assertFalse(any(method == "PATCH" for _, method, _ in calls))

    def test_immutable_misconfiguration_stops_before_pointer_update(self):
        calls = self.publication(immutable=False)
        self.assertFalse(any(path == "releases/2" for path, _, _ in calls))


if __name__ == "__main__":
    unittest.main()
