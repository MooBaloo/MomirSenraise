"""Release planning and verification. No signing credentials are read by Gradle."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

REPOSITORY = "MooBaloo/MomirSenraise"
PACKAGES = {"stable": "io.github.moobaloo.momir", "dev": "io.github.moobaloo.momir.dev"}
MARKER = re.compile(r'<!-- momir-release (\{[^\n]+\}) -->')
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def require(value, message):
    if not value:
        raise ValueError(message)


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def api(path, method="GET", data=None):
    args = ["gh", "api", "--method", method, f"repos/{REPOSITORY}/{path}"]
    if data is not None:
        args += ["--input", "-"]
    return json.loads(command(*args, input=json.dumps(data) if data is not None else None) or "null")


def releases():
    result = []
    for page in range(1, 1001):
        batch = api(f"releases?per_page=100&page={page}")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError("Release inventory exceeds supported pagination")


def receipt(release):
    match = MARKER.search(release.get("body") or "")
    require(match, "Existing release lacks a version receipt; reconcile it before publishing")
    return json.loads(match[1])


def allocate(run, attempt):
    require(run > 0 and 1 <= attempt <= 99, "Invalid run/attempt allocation")
    code = run * 100 + attempt
    require(code <= 2_100_000_000, "versionCode allocation exhausted")
    return code


def shipped(path):
    return ((path.startswith("app/src/") and not path.startswith(("app/src/test/", "app/src/androidTest/"))) or
            path in {"app/build.gradle.kts", "app/proguard-rules.pro", "build.gradle.kts", "settings.gradle.kts",
                     "gradle.properties", "release/version.txt"} or
            path.startswith("gradle/"))


def identity(channel, source, version, code):
    require(channel in PACKAGES and SHA.fullmatch(source), "Invalid source/channel")
    require(VERSION.fullmatch(version), "release/version.txt must contain X.Y.Z")
    require(isinstance(code, int) and 1 <= code <= 2_100_000_000, "Invalid versionCode")
    name = version if channel == "stable" else f"{version}-dev.{code}.g{source[:12]}"
    return dict(channel=channel, source=source, versionCode=code, versionName=name,
                package=PACKAGES[channel], label="Momir" if channel == "stable" else "Momir Dev",
                tag=f"v{version}" if channel == "stable" else f"dev-{code}-{source[:12]}")


def current(channel, source):
    branch = "main" if channel == "stable" else "dev"
    state = api(f"branches/{branch}")
    require(state["protected"], f"{branch} must be protected")
    require(state["commit"]["sha"] == source, "Source is no longer the channel branch head")


def check_new(meta, existing):
    for release in existing:
        if release["tag_name"] == meta["tag"]:
            raise ValueError("Tag/release already exists; never replace a published build")
        if release["draft"] or release["tag_name"] == "development":
            continue
        if release["tag_name"].startswith(("v", "dev-")):
            old = receipt(release)
            if old["channel"] == meta["channel"]:
                require(old["versionCode"] < meta["versionCode"], "Refusing a stale versionCode")


def plan(output):
    require(os.environ["GITHUB_REPOSITORY"] == REPOSITORY, "Unexpected repository")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    ref = os.environ["GITHUB_REF"]
    require(ref in ("refs/heads/main", "refs/heads/dev"), "Not a release branch")
    channel = "stable" if ref.endswith("/main") else "dev"
    source, before = event["after"], event["before"]
    require(SHA.fullmatch(source) and SHA.fullmatch(before), "Invalid push revisions")
    if before == "0" * 40:
        Path(output).write_text("enabled=false\n")
        return  # Branch creation is not a release request.
    require(command("git", "rev-parse", "HEAD") == source, "Checkout mismatch")
    subprocess.run(["git", "merge-base", "--is-ancestor", before, source], check=True)
    paths = command("git", "diff", "--name-only", before, source).splitlines()
    if not any(map(shipped, paths)):
        Path(output).write_text("enabled=false\n")
        return
    current(channel, source)
    trusted = api("branches/main")["commit"]["sha"]
    # GITHUB_TOKEN cannot create release tags at dev commits with workflow changes
    # relative to the default branch. Fail rather than request broader credentials.
    changed_workflows = command("git", "diff", "--name-only", trusted, source, "--", ".github/workflows")
    require(not changed_workflows, "Promote workflow changes to main before dev publication")
    if channel == "stable":
        prs = api(f"commits/{source}/pulls?per_page=100")
        promotions = [p for p in prs if p.get("merged_at") and p.get("merge_commit_sha") == source
                      and p["base"]["ref"] == "main" and p["head"]["ref"] == "dev"
                      and p["head"]["repo"] and p["head"]["repo"]["full_name"] == REPOSITORY]
        require(len(promotions) == 1, "Stable publication requires a merged dev-to-main promotion")
        pr = promotions[0]
        reviews = api(f"pulls/{pr['number']}/reviews?per_page=100")
        require(any(r["state"] == "APPROVED" and r["commit_id"] == pr["head"]["sha"]
                    for r in reviews), "Promotion lacks exact-head approval")
    code = allocate(int(os.environ["GITHUB_RUN_NUMBER"]), int(os.environ["GITHUB_RUN_ATTEMPT"]))
    meta = identity(channel, source, Path("release/version.txt").read_text().strip(), code)
    check_new(meta, releases())
    Path("build/release").mkdir(parents=True, exist_ok=True)
    Path("build/release/identity.json").write_text(json.dumps(meta, indent=2) + "\n")
    Path(output).write_text(f"enabled=true\nchannel={channel}\ncode={code}\nsource={source}\ntrusted={trusted}\n")


def verify_apk(apk, meta, aapt):
    text = command(aapt, "dump", "badging", str(apk))
    package = re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", text)
    require(package and package.groups() == (meta["package"], str(meta["versionCode"]), meta["versionName"]),
            "APK package/version mismatch")
    require("application-debuggable" not in text, "Release APK is debuggable")
    require(f"application-label:'{meta['label']}'" in text, "Launcher label mismatch")
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate ZIP entries")
        info = archive.getinfo("assets/build-identity.json")
        require(info.file_size < 4096, "Oversized build identity")
        embedded = json.loads(archive.read(info))
    require(embedded == {k: meta[k] for k in ("source", "channel", "versionCode", "versionName")},
            "Embedded source identity mismatch")


def fingerprint(value):
    value = value.replace(":", "").lower()
    require(re.fullmatch(r"[0-9a-f]{64}", value), "Expected certificate fingerprint is not configured")
    return value


def verify_signature(apk, expected, apksigner):
    text = command(apksigner, "verify", "--verbose", "--print-certs", str(apk))
    found = re.findall(r"Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]+)", text)
    require(len(found) == 1 and fingerprint(found[0]) == fingerprint(expected), "Unexpected APK signer")


def verify_receipt(meta):
    require(meta["channel"] == os.environ["EXPECTED_CHANNEL"], "Artifact channel mismatch")
    require(meta["source"] == os.environ["EXPECTED_SOURCE"], "Artifact source mismatch")
    require(meta["versionCode"] == int(os.environ["EXPECTED_CODE"]), "Artifact allocation mismatch")
    base = meta["versionName"].split("-dev.")[0]
    expected = identity(meta["channel"], meta["source"], base, meta["versionCode"])
    require(all(meta[k] == v for k, v in expected.items()), "Artifact identity mismatch")


def sign(directory):
    directory = Path(directory)
    meta = json.loads((directory / "identity.json").read_text())
    verify_receipt(meta)
    expected = fingerprint(os.environ["EXPECTED_CERT_SHA256"])
    require(expected != fingerprint(os.environ["OTHER_CHANNEL_CERT_SHA256"]), "Channels must use different keys")
    sdk = Path(os.environ["ANDROID_HOME"]) / "build-tools" / "34.0.0"
    unsigned = directory / "unsigned.apk"
    verify_apk(unsigned, meta, str(sdk / "aapt"))
    signed = directory / "momir.apk"
    # Only fixed SDK tools run while a temporary signing key exists; no Gradle.
    with tempfile.TemporaryDirectory() as temp:
        key = Path(temp) / "signing.jks"
        key.write_bytes(base64.b64decode(os.environ["SIGNING_KEYSTORE_BASE64"], validate=True))
        key.chmod(0o600)
        aligned = Path(temp) / "aligned.apk"
        subprocess.run([str(sdk / "zipalign"), "-f", "4", str(unsigned), str(aligned)], check=True)
        subprocess.run([str(sdk / "apksigner"), "sign", "--ks", str(key),
                        "--ks-key-alias", os.environ["SIGNING_KEY_ALIAS"],
                        "--ks-pass", "env:SIGNING_STORE_PASSWORD", "--key-pass", "env:SIGNING_KEY_PASSWORD",
                        "--out", str(signed), str(aligned)], check=True)
    verify_signature(signed, expected, str(sdk / "apksigner"))
    verify_apk(signed, meta, str(sdk / "aapt"))
    meta["certificateSha256"] = expected
    meta["apkSha256"] = hashlib.sha256(signed.read_bytes()).hexdigest()
    (directory / "identity.json").write_text(json.dumps(meta, indent=2) + "\n")
    (directory / "momir.apk.sha256").write_text(f"{meta['apkSha256']}  momir.apk\n")
    unsigned.unlink()


def release_body(meta):
    return (f"Source: `{meta['source']}`\n\nVersion: `{meta['versionName']}` / code `{meta['versionCode']}`\n\n"
            f"Package: `{meta['package']}`\n\nAPK SHA-256: `{meta['apkSha256']}`\n\n"
            f"Certificate SHA-256: `{meta['certificateSha256']}`\n\n"
            "Installation is a separate decision.\n\n"
            f"<!-- momir-release {json.dumps(meta, sort_keys=True)} -->")


def publish(directory):
    directory = Path(directory)
    meta = json.loads((directory / "identity.json").read_text())
    verify_receipt(meta)
    expected = fingerprint(os.environ["EXPECTED_CERT_SHA256"])
    require(meta["certificateSha256"] == expected, "Receipt signer mismatch")
    require(meta["apkSha256"] == hashlib.sha256((directory / "momir.apk").read_bytes()).hexdigest(), "APK digest mismatch")
    sdk = Path(os.environ["ANDROID_HOME"]) / "build-tools" / "34.0.0"
    verify_signature(directory / "momir.apk", expected, str(sdk / "apksigner"))
    verify_apk(directory / "momir.apk", meta, str(sdk / "aapt"))
    current(meta["channel"], meta["source"])
    existing = releases()
    check_new(meta, existing)
    release = api("releases", "POST", dict(tag_name=meta["tag"], target_commitish=meta["source"],
                  name=meta["versionName"], body=release_body(meta), draft=True,
                  prerelease=meta["channel"] == "dev", make_latest="false"))
    for name in ("momir.apk", "momir.apk.sha256", "identity.json"):
        subprocess.run(["gh", "release", "upload", meta["tag"], str(directory / name), "--repo", REPOSITORY], check=True)
    staged = api(f"releases/{release['id']}")
    require({a["name"] for a in staged["assets"]} == {"momir.apk", "momir.apk.sha256", "identity.json"}, "Incomplete release assets")
    for asset in staged["assets"]:
        digest = hashlib.sha256((directory / asset["name"]).read_bytes()).hexdigest()
        require(asset.get("digest") == f"sha256:{digest}" and asset["state"] == "uploaded", "Uploaded asset mismatch")
    current(meta["channel"], meta["source"])
    published = api(f"releases/{release['id']}", "PATCH", dict(draft=False,
                    make_latest="true" if meta["channel"] == "stable" else "false"))
    require(published.get("immutable") is True, "Release immutability is not enabled; publication setup is invalid")
    if meta["channel"] == "dev":
        links = (f"Latest verified development build: [{meta['versionName']}]({published['html_url']})\n\n"
                 f"[Download APK]({published['html_url'].replace('/tag/', '/download/')}/momir.apk)\n\n"
                 "This is a rolling channel index. Its fixed tag is not the build source; use the exact source below.\n\n" + release_body(meta))
        pointers = [r for r in existing if r["tag_name"] == "development"]
        if pointers:
            pointer = pointers[0]
            require(pointer["prerelease"] and not pointer["draft"] and pointer.get("immutable") is True,
                    "Development index has incompatible settings")
            require(receipt(pointer)["versionCode"] < meta["versionCode"], "Refusing stale development index")
            api(f"releases/{pointer['id']}", "PATCH", dict(body=links))
        else:
            pointer = api("releases", "POST", dict(tag_name="development", target_commitish=meta["source"],
                          name="Development preview", body=links, draft=True, prerelease=True, make_latest="false"))
            pointer = api(f"releases/{pointer['id']}", "PATCH", dict(draft=False, make_latest="false"))
            require(pointer.get("immutable") is True, "Development index must also be immutable")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "sign", "publish"))
    parser.add_argument("path")
    args = parser.parse_args()
    {"plan": plan, "sign": sign, "publish": publish}[args.action](args.path)
