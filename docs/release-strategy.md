# Release strategy

Build and signing run on GitHub-hosted infrastructure. `dev` is the enduring
integration branch; `main` is the stable release branch. Installation remains a
separate decision from source integration and artifact publication.

## Channels and versions

Stable and development builds use distinct application IDs and distinct signing
keys. Development builds are labelled **Momir Dev**, with no version or source
revision in the launcher name. About shows the full version, versionCode and
exact built commit; App info exposes the versionName. Preserve each channel's
package and signing identity after first publication. Keep keys outside source
control and ordinary pull-request CI.

Use a monotonic versionCode independent of the source SHA. Allocate a new code
for each distributed build, including retries and recovery builds; never reuse
a published code. Stable versions use X.Y.Z, initially in the 0.x.y series.
Development versions identify the build and source. Concrete identities and the
allocation mechanism are specified in [Builds and signing](builds-and-signing.md).
See Android's [identity](https://developer.android.com/build/configure-app-module),
[signing](https://developer.android.com/studio/publish/app-signing) and
[versioning](https://developer.android.com/studio/publish/versioning) guidance.

## Review and release authorization

Feature PRs integrate into protected `dev` after required review and tests.
Record exact source and artifact identity, CI, relevant security assessment and
manual results. Keep the promotion candidate fixed during acceptance and repeat
affected checks after changes. Validate combined application behavior relevant
to a release; a calibration print alone is not full application acceptance.

A maintainer-approved, tested `dev` → `main` promotion authorizes automatic
stable signed and verified publication for shipped application changes, including
code, resources and dependencies. There is no second release-approval button.
Documentation-only changes do not publish an APK. Development pushes publish
signed previews automatically without routine maintainer approval of each build.
Repository protections still govern source integration.

## Publication and traceability

Build without signing credentials, sign in an isolated job, then independently
verify the APK identity, certificate and checksums before publication. Stable
releases use immutable version tags and assets. Record the exact commit,
version/code, package, certificate fingerprint, APK SHA-256 and validation limits.
Do not replace published stable artifacts.

The public **Development preview** channel provides a coherent latest-build entry
point with exact source/version/checksum evidence. Its proposed implementation
uses an index linking to immutable per-build prereleases; see the setup decisions
in [Builds and signing](builds-and-signing.md). Upload and verify a complete new
build before changing the index. Failed or stale runs must not replace it.
[GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
protect tags/assets while permitting release-note updates.

## Installation and recovery

Publication does not install or deploy an application. When a build is installed
for review, identify its artifact and who approves replacement or removal. Keep
it and its data through approval and after merge until that approval is given.
Retain artifacts and evidence for verification and recovery.

Before updating an installation, verify channel identity, data/schema compatibility
and applicable device-management continuity. Establish an approved recovery plan.
Prefer a compatible higher-code forward recovery; source reversion cannot undo
data changes and uninstall/reinstall must not be assumed to preserve state.

The publishing workflow remains disabled until signing custody, branch and
environment restrictions, tag rules and release immutability have been verified.
This document and the foundation code do not configure those settings.
