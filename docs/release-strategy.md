# Release strategy

Source integration, release publication and installation are separate decisions.
A merged pull request does not publish or deploy an application. This strategy
sets release requirements; it does not configure signing or authorize changes
to an installation.

## Identity, channels and versions

Preserve the established application ID and signing identity when updating an
existing release. Verify the signing path and expected certificate before
preparing release artifacts. Keep signing keys outside the repository and
ordinary pull-request CI.

Use a separate application ID and data storage for development builds. Debug
APKs support testing and are not release artifacts. Release candidates use
release settings and undergo the same identity and signing verification as final
releases. Changing package identity does not transfer data or device privileges.
See Android's [application identity](https://developer.android.com/build/configure-app-module)
and [signing](https://developer.android.com/studio/publish/app-signing) guidance.

Maintain a versionCode allocation record. Each newly distributed release-channel
APK, including candidates and recovery builds, receives the next unused code
above previously allocated codes. Use `0.x.y` version names until product
stability, `-rc.N` for release candidates and source revision labels for development
builds. A final release following a candidate receives a higher versionCode.
See [Android versioning](https://developer.android.com/studio/publish/versioning).

## Review and acceptance

Follow the repository's contribution instructions and protected-branch rules.
Record the exact reviewed commit, passing CI, independent review, security
assessment where relevant and applicable manual results. Resolve findings and
obtain required approvals before merging; refresh affected evidence after changes.

Before a release, agree its scope and acceptance criteria and hold the candidate
fixed during review. Validate the combined application behavior affected by the
release, including relevant card loading, rendering and printing. A calibration
print alone does not establish full application acceptance. Record limitations
and untested paths explicitly. Automated checks cannot replace relevant hardware
tests, and debug-build acceptance does not validate different release bytes.

This strategy does not select a permanent integration-branch model. Direct feature
PRs and staged promotion can both support these gates; contributors should follow
the current README rather than infer new branch rules. See [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow)
and [protected-branch rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

## Publication and traceability

Select an accepted `main` commit, build a non-debuggable APK and sign through the
approved release process. Verify its signature, package, versions and acceptance
before publication. Publish deliberately with a matching `v<versionName>` tag,
full source commit, package/versionCode, certificate fingerprint, APK SHA-256,
changes, acceptance results and known limits. Preserve published tags and
artifacts. Publication does not trigger installation; no automatic deployment
is planned. See [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

## Test installations and recovery

When a build is installed for manual review, identify its exact artifact and
record who will approve its replacement or removal. Retain the build, test data
and evidence through review and after merge until that approval is given.
Merging is not an instruction to uninstall a test build or restore an earlier
installation. Keep release artifacts and acceptance evidence available for
verification and recovery.

Before updating an installation, validate package/signer and data/schema
compatibility and, where applicable, Device Owner component continuity. Establish
an approved recovery plan. Retain prior artifacts but prefer compatible
higher-code forward recovery: Android normally blocks downgrades, source
reversion cannot undo data migration, and uninstall/reinstall must not be assumed
to preserve data or device management.
