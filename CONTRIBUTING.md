# Contributing

Start a focused branch from `main` and open a pull request against this fork.
Describe the change, validation results, and any remaining limitations. Keep
unrelated changes separate and leave merging to the maintainer.

## Build and test

Use JDK 21 and the Android SDK with platform 34 and build-tools 34.0.0. Set
`ANDROID_HOME` to your SDK directory, then run from the repository root:

```sh
bash ./gradlew --no-daemon :app:assembleDebug :app:testDebugUnitTest
```

The wrapper is invoked with Bash because it is not executable in the repository.
The current app has no unit test sources, so the unit-test task reports
`NO-SOURCE`; a successful run verifies the debug build, not behavioral coverage.
CI runs these tasks in one Linux job for pull requests and pushes to `main`,
cancels superseded runs, and uploads no artifacts. No device or card corpus is
needed for this check.

For runtime changes, describe relevant manual checks on supported hardware.
Building the APK does not validate printing, camera, or other device behavior.
See [README.md](README.md) and [docs/](docs/) for the app's existing documentation.

## Scope and attribution

Preserve the MIT license, including `Copyright (c) 2026 MagieAlex`, and existing
attribution. Do not commit credentials, signing material, personal identifiers,
or private operational content. Review the complete diff before publishing.
