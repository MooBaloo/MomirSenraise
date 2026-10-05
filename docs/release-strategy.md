# Release strategy

This strategy separates source integration, release publication and installation.
Signing configuration and migration staging remain planned work; documenting the
strategy does not implement them or authorize changes to an installation.

## Identity and build channels

Preserve the established application ID and signing identity when updating an
existing release. Before preparing an update, verify the signing path and expected
certificate, data compatibility and supported upgrade path. Keep signing keys
outside the repository and ordinary pull-request CI.

Use a separate application ID and data storage for development builds. Verify
package and signer compatibility before replacing any installed test build.
Debug APKs are test candidates, not release artifacts. Checks requiring privileged
appliance capabilities need a representative, separately authorized installation.
Changing package identity does not transfer application data or device privileges.

Maintain a versionCode allocation record and choose the next unused value above
previously allocated release-channel codes, including candidates and recovery
builds. Each newly distributed release-channel APK receives a higher code. Use
`0.x.y` version names until product stability, `-rc.N` for release candidates and
source revision labels for development builds. See Android's [application identity](https://developer.android.com/build/configure-app-module),
[signing](https://developer.android.com/studio/publish/app-signing) and
[versioning](https://developer.android.com/studio/publish/versioning) documentation.

## Migration staging and review

Use temporary protected `dev` integration for selected migration features,
keeping `main` unchanged until combined acceptance. Keep feature PRs focused and
preserve license attribution. Before using `dev`, create it from `main`, verify
protections, add push CI and update contribution guidance.

Require maintainer approval, code-owner review, stale-review dismissal, resolved
discussions and strict build/test checks on both branches, with no bypass, force
pushes or deletion. Feature PRs record the exact reviewed commit, CI results,
independent review, security assessment where relevant and scoped hardware
evidence. Refresh affected evidence after changes.

Agree the feature list and acceptance criteria before a `dev` to `main` promotion;
hold the candidate fixed for review. Validate combined card loading, rendering,
printing and behavior affected by the selected features. A calibration print
alone is not full application acceptance. Promote only after required checks,
reviews and agreed tests pass. Preserve shared ancestry when promoting and
synchronizing branches.

After the first accepted combined release, reassess permanent `dev`. Short feature
PRs into protected `main` with deliberate release tags can reduce synchronization
overhead when changes are independently releasable. See [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow)
and [rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

## Publication and installation

Select an accepted `main` commit, build a non-debuggable APK, sign through the
approved release process, and verify its identity and release-binary acceptance.
Publish deliberately with a matching `v<versionName>` tag, full source commit,
package/versionCode, certificate fingerprint, APK SHA-256, changes, acceptance and
known limits. Preserve published tags and artifacts. Debug acceptance does not
validate different release bytes. Publication does not trigger installation; no
automatic deployment is planned. See [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Retain the current authorized review build and its data through approval and
after merge until removal or replacement is agreed with the reviewers and the
installation's operator. Keep its exact artifact and evidence available and
maintain exclusive printer access. Merge does not trigger cleanup or restoration
of a previous installation.

Before updating an installation, validate data/schema compatibility and, where
applicable, Device Owner component continuity, with an approved recovery plan.
Retain prior artifacts but prefer compatible higher-code forward recovery:
Android normally blocks downgrades, source reversion cannot undo data migration,
and uninstall/reinstall must not be assumed to preserve data or device management.

## Current setup and next steps

Currently, `dev` does not exist and only `main` is protected. Contributions follow
the existing README instructions until staging is configured. Verify signing
availability, expected release identity and version allocations first; define the
migration feature list and acceptance criteria, then implement the necessary
build-channel and branch/CI/protection setup through reviewed changes. Release
signing, publication and installation each require separate authorization.
