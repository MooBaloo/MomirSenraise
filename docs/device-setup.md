# H10S prerequisites and physical acceptance

This fork targets a Senraise H10S with 58 mm thermal paper. Sunmi devices and
printer services are unsupported. The current printing port has a limited
physical test result, not full release acceptance.

Build with JDK 21, Android platform 34, build-tools 34.0.0 and NDK 26.1.10909125:

```sh
bash ./gradlew --no-daemon :app:assembleDebug :app:testDebugUnitTest
```

Set `ANDROID_HOME` to the SDK directory. The APK contains `armeabi-v7a` and
`arm64-v8a` UART libraries. Printer availability requires normal process access
to the H10S UART and control nodes described in [H10S printing](h10s-printer.md).
No root command, permission workaround or Device Owner setup is performed by
the application.

## Installation boundary

This port retains upstream application ID `software.zeasy.momir`, version fields,
and ordinary debug signing. It defines no official signing or upgrade procedure,
no isolated review package, and no kiosk policy. Do not assume its APK can safely
replace an existing appliance installation. Installation needs a separately
approved package, signer, existing-data and competing-printer-service plan.
The card database and artwork formats are unchanged; no corpus migration is part
of this printing change.

## Load a corpus

After a separately approved installation, open the app once to initialize its
files directory. Prepare `momir.db` and `art.pack` using the
[corpus builder](../tools/momirdeck/README.md), then verify the installed APK’s
application ID and the intended adb target before transferring anything.
From `tools/momirdeck/`, the transfer command is:

```sh
python momirdeck.py push --package 'YOUR.APPLICATION.ID'
```

Replace the placeholder with the verified package. The command transfers the
output files that exist to `/sdcard/Android/data/<verified-application-id>/files/`.
Confirm both `momir.db` and `art.pack` were built for a complete corpus, and
check the transfer output for `skip ... (missing)` messages: `Done` alone does
not prove both files were transferred. Do not assume another build channel or
existing installation shares that path.

Close the app completely and reopen it after transfer; the database is opened
at activity creation, and opening Settings alone does not retry loading it.
Then check Settings diagnostics for corpus counts before trying a preview. An
empty category may mean the corpus contains no cards of that type.

Long-press PRINT to render `preview.png` in the app’s files directory without
sending a printer job. A good preview checks layout, not UART operation or
physical paper length. For scanner checks, grant the app’s camera permission
and record preview/focus/QR results separately from printer tests.

## Validation scope

A calibration-slip print passed on an H10S at source commit
`b26d0a798cc489dfc413d38ba4e69ecf79c46b11`, with terminal outcome `SUCCEEDED`.
The APK SHA-256 was
`f5aff463facf3a8ce76d67b0cf90cdfcb169ec7944a251410602265c83671b55`.
See the [recorded test result](https://github.com/MooBaloo/MomirSenraise/pull/2#issuecomment-5988851866).
This establishes that test slip on that installation. It does not establish
full-corpus card printing, camera scanning, resync, calibrated dimensions,
long/dense jobs, cancellation or fault recovery. Repeat relevant checks for
the APK being considered for release.

## Physical acceptance checklist

On a separately authorized candidate installation, record the exact commit and
APK identity, device model/firmware, access readiness, and results. Capture the
previous `lastJob` number, then the new terminal outcome and `lastJobDetail`
after the test (close and reopen Settings to refresh); preserve the matching `H10sPrinter` terminal log. An idle
`not connected` state alone is not a result:

- Print a calibration slip; measure the head-to-tear distance, foot margin,
  width and total length. The inherited 12 mm/5 mm defaults are not H10S measurements.
- Print short and dense artwork/QR slips, repeated copies, and rapid consecutive
  jobs. Check for truncation, stray commands, gaps and duplicate output.
- Exercise paper-out/replacement and safe temperature-status reporting. Verify
  failure messages and that a later user request works after idle cleanup.
- Cancel during a long job and navigate between activities. Confirm no replay,
  no interleaved bytes, and eventual idle release without cutting off output.
- Verify an unavailable/occupied UART fails without changing shared controls.
  Confirm other printer software cannot interfere; `flock` alone is advisory.
- Test long jobs and flow-control stalls against the bounded drain and five-second
  grace. If output is cut off, do not accept the release policy as validated.

Camera behavior, device policy and kiosk operation are not established by these
printer tests. Existing documentation screenshots are inherited UI/layout
examples, not photographs proving this port's H10S output.
