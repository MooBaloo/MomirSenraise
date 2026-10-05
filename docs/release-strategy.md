# Release strategy

Direction agreed with the owner on 2026-10-05. Signing details and migration
setup remain pending; this policy does not authorize configuration or device
changes. Source integration, release publication and installation are separate
decisions.

## Identity and channels first

Preserve Official's application ID `io.github.moobaloo.momirsenraise` and
established signing certificate. Verify an authorized signing path against the
trusted certificate before promising a release; do not generate a replacement
key. Keep keys outside the repository and ordinary PR CI.

Retain `io.github.moobaloo.momirsenraise.debug` for isolated development, checking
installed signer/data compatibility before reuse. Debug APKs are test candidates,
not Official releases. The upstream-derived `software.zeasy.momir` code 1 candidate
is a separate app, not an update to Official code 11. Device Owner acceptance
requires a separately approved compatible Official update, not a new review
package or ownership-transfer scheme. Package and certificate continuity alone
do not establish data or Device Owner component compatibility.

Reconcile previously allocated versionCodes before assigning the next unused
Official code above 11; do not assume 12 is available. Each newly distributed
Official candidate, release or recovery artifact receives a higher code. Continue
`0.x.y` version names until product stability, with `-rc.N` for release candidates
and source revision labels for development builds. Android identity, signing and
versioning are described in the official [identity](https://developer.android.com/build/configure-app-module),
[signing](https://developer.android.com/studio/publish/app-signing) and
[versioning](https://developer.android.com/studio/publish/versioning) documentation.

## Migration and review

Use temporary protected `dev` integration for selected migration ports, keeping
`main` unchanged until combined acceptance. Port focused public changes and
preserve MIT attribution; never import private history or the whole private tree.
Before using `dev`, create it from public `main`, verify protections, add push CI
and update contribution guidance through separately authorized work.

Use owner/code-owner approval, stale-review dismissal, resolved discussions,
strict build/test checks and no bypass, force pushes or deletion on both branches.
Feature PRs record exact-head CI, relevant independent review/security assessment
and scoped hardware evidence. Refresh affected evidence after changes. Agree the
selected feature list and combined acceptance before a `dev` to `main` promotion;
hold its candidate fixed for owner review. Include real card loading, rendering,
printing and checks relevant to selected features. A Test Slip alone is not full
application acceptance. Promote only after required checks, reviews and agreed
tests pass. Preserve shared ancestry when promoting and synchronizing branches.

After the first accepted combined release, reassess permanent `dev`: short feature
PRs into protected `main` with deliberate tags may suit single-owner maintenance
with less synchronization overhead. See [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow)
and [rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

## Release and installed state

Select an accepted `main` commit, build a non-debuggable APK, sign through the
approved owner-controlled path, and verify its identity and release-binary
acceptance. Publish deliberately with a matching `v<versionName>` tag, full source
commit, package/versionCode, certificate fingerprint, APK SHA-256, changes,
acceptance and known limits. Preserve published tags and artifacts. Debug
acceptance does not validate different release bytes. No automatic deployment
or wholesale import of prior release automation is needed. See [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Keep an authorized current review build and its data installed through approval
and after merge until the owner requests removal or accepts a replacement;
retain its exact artifact and evidence and maintain printer isolation. Merge
does not trigger cleanup, Official restoration or installation.

Before an Official update, approve data/schema and Device Owner compatibility
and a recovery plan. Retain prior artifacts but prefer compatible higher-code
forward recovery: Android normally blocks downgrades, source reversion cannot
undo data migration, and uninstall/reinstall must not be assumed to preserve data
or ownership.

## Pending implementation

Confirm signing availability, the trusted certificate and allocated codes; settle
the selected migration features and combined acceptance. Then authorize the
minimal build-identity and branch/CI/protection setup. At adoption, `dev` does not
exist and only `main` is protected. PR #2 remains held for owner approval and
separately authorized retargeting after staging is ready. No release automation,
signing configuration or device change is introduced by this document.
