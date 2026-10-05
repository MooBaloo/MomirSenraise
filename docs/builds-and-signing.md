# Builds and signing

This foundation is disabled until the setup below is reviewed and completed.
It creates no signing keys, environments, branch rules or installations.

## Channels and identity

The proposed new package identities are:

| Channel | Package | Launcher label | Signing |
| --- | --- | --- | --- |
| Stable | `io.github.moobaloo.momir` | Momir | Dedicated stable key |
| Development preview | `io.github.moobaloo.momir.dev` | Momir Dev | Separate development key |
| Local debug | `io.github.moobaloo.momir.dev.debug` | Momir Dev | Android debug signing |

Package names require approval before the first signed build. These are separate
applications with independent data. An installed package using a different key
cannot be updated with these builds. Preserve each channel's identity after its
first publication. Source namespace is independent of application identity.

Settings → About shows version, versionCode, channel and the full built source
SHA. Local modified checkouts say `modified`; publication requires a clean exact
checkout. App info uses the versionName. No version or SHA appears in launcher
labels. Existing UI screenshots predate the About action and do not demonstrate
this build identity surface; physical UI validation remains pending.

## Credential-free builds

Use JDK 21, Android SDK 34 and build-tools 34.0.0:

```sh
python3 -m unittest discover -s tools/release -p 'test_*.py'
bash ./gradlew --no-daemon :app:assembleDebug :app:testDebugUnitTest
bash ./gradlew --no-daemon :app:assembleRelease -PbuildChannel=dev
bash ./gradlew --no-daemon :app:assembleRelease -PbuildChannel=stable
```

Release APKs are unsigned. Gradle never receives a release signing key. The
release workflow supplies `publishBuild`, `buildSourceRevision` and
`buildVersionCode`; it verifies the embedded source, package, version and label
before signing and again before publication. Local debug builds are never
published by this workflow.

## Version allocation

`release/version.txt` supplies the stable X.Y.Z version; a shipped stable change
requires a new version in its promotion PR. Development versionName adds
`-dev.<code>.g<short-SHA>`. Full SHA is also embedded in the APK and receipt.

The single `Build signed channels` workflow allocates versionCode as
`100 * github.run_number + github.run_attempt`, with attempts restricted to 1–99
and the Android code ceiling enforced. Codes are independent of SHA, increase
between runs and retries, and may have gaps. Do not rename/recreate this workflow
or reset its allocation without reconciling all published channel codes.
Publication rejects existing tags and codes no newer than published builds in
that channel. A stale rerun cannot replace a newer build.

## Automatic build and publication

Protected `dev` pushes build and sign development previews without routine manual
approval. An approved, tested `dev` → `main` promotion authorizes automatic stable
publication when shipped inputs change. There is no second release-approval button.
Both paths run tests and build the unsigned release variant on GitHub-hosted
runners. Planning runs reviewed tooling pinned from `main` on a separate runner,
using
Python isolated mode so source-branch modules cannot be imported. The unsigned
build cannot select that trusted revision or provide privileged-job outputs.
Signing uses a separate fresh runner and the same pinned tooling from `main`;
only fixed Android tools run while the key is present. Publication uses another
job with no signing secrets. PR CI has read-only repository access and no keys.

Changes under packaged application sources, build/dependency configuration or
`release/version.txt` trigger publication. Documentation/tests/workflow-only
changes do not. Branch creation does not publish. A newer shipped change
supersedes an older source; a descendant containing
only non-shipped changes may still publish the original approved source.
Ambiguous or truncated comparisons stop publication. Only publication jobs
enter the per-channel concurrency queue, so docs-only pushes cannot supersede
pending application releases. A newer application publication may supersede a
pending one; an in-flight publication is not cancelled.

Immutable per-build prereleases hold development APKs and receipts. The public
**Development preview** prerelease at tag `development` is a rolling index:
its notes link directly to the newest complete, verified build and record exact
SHA, version and checksum. Its fixed tag is not the current build source. The
index has no replaceable APK asset; its notes change only after the new build's
assets are verified and published. This channel presentation needs approval.
It works with [GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases),
which allow note edits but prohibit moving tags or replacing assets. Stable
`vX.Y.Z` releases and per-build `dev-<code>-<SHA>` releases are never overwritten.

Uploads first go to a draft release. All asset digests must match before the draft
is published. A failure leaves the prior index intact and may leave a draft for
inspection; do not delete or replace published assets to retry. An existing draft
or bare tag deliberately blocks retries until maintainers reconcile the failed run.
The workflow rejects pre-existing build tags, creates the exact source tag,
and verifies its target before uploading and before publishing. The workflow
checks the resulting release's `immutable` status and stops on a
misconfigured repository. This check does not undo publication: enabling and
verifying repository immutability is a required setup gate, not an optional step.

GitHub restricts creating release tags when the target changes workflow files
relative to the default branch. This implementation stops dev publication when
that difference exists; it does not request a broader token. Promote reviewed
workflow changes to `main` before publishing affected dev builds. See the
[release API](https://docs.github.com/en/rest/releases/releases).

## Required setup before enabling

- Approve the package names and rolling-index presentation. Confirm the first
  stable version and allocation policy.
- Create and protect `dev`; require PRs, passing build/tests, resolved reviews,
  and no force-push/deletion/bypass. Agree reviewer assignments for routine dev
  integration. Require maintainer/code-owner review for release workflows and
  tooling on both branches: the workflow definition itself is a privileged trust
  boundary, even though source-branch planner code is never executed. Protect
  `main` with maintainer/code-owner approval of the latest
  reviewed promotion and required tests. The workflow checks protected status
  and promotion approval, but does not configure or fully audit these rules.
- Verify repository release immutability is enabled. The implementation review
  could not read that setting with its available permissions. Review tag rules
  for `v*`, `dev-*` and `development`: allow authorized creation, prohibit moving
  or deleting published tags, and do not add a protection bypass for the workflow.
- Independently provision two new signing keys and secure recovery copies.
  Establish GitHub environments `signing-stable` (only `main`) and `signing-dev`
  (only `dev`), with no routine required-reviewer gate. Exact restrictions require
  security review before use; never allow PR refs. No secret is provisioned here.
- Each environment needs `SIGNING_KEYSTORE_BASE64`, `SIGNING_KEY_ALIAS`,
  `SIGNING_STORE_PASSWORD` and `SIGNING_KEY_PASSWORD`. Base64 is transport encoding,
  not encryption. Set public repository variables `STABLE_CERT_SHA256` and
  `DEV_CERT_SHA256`; the fingerprints must differ. Key files exist only in a
  temporary signing directory and are not uploaded or cached.
- Verify these gates before setting `RELEASE_IMMUTABILITY_VERIFIED=true` and
  `RELEASE_AUTOMATION_ENABLED=true`. These flags are setup controls, not a routine
  approval step. Validate the signed channel artifacts before operational use.

Only publication receives `contents: write`; the other jobs use read access.
Actions are pinned by commit, credentials are not persisted by checkout, and no
privileged PR trigger or automatic installation exists. Consult [GitHub environment
restrictions](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
and [Actions security guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## Artifact verification and installation

Each release contains `momir.apk`, `momir.apk.sha256` and `identity.json`. Before
installation, verify the checksum, Android signature and expected channel
certificate, package and version. `apksigner verify --verbose --print-certs` checks
the signature; compare its certificate fingerprint with the approved public value.
The workflow aligns before signing and makes no changes to signed APK bytes.
See [Android apksigner](https://developer.android.com/tools/apksigner).

Installation and test-build removal remain separately authorized operations.
A release or merge does not install, uninstall, change device policy or replace
an existing application. Signing and publication have not been exercised with
production keys by this credential-free foundation.
