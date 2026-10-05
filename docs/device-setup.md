# H10S prerequisites and physical acceptance

This fork targets a Senraise H10S with 58 mm thermal paper. Sunmi devices and
printer services are unsupported. The current printing port is a source/build
candidate, not a physically accepted release.

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
