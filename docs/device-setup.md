# Device setup

## What you need

- A **Sunmi V2** (or another Sunmi handheld with the built-in 58 mm printer and
  the `woyou.aidlservice.jiuiv5` service).
- 58 mm thermal paper.
- **JDK 17, 21 or another version Gradle 8.9 supports**, and the Android SDK
  with platform 34. Not necessarily the JDK bundled with Android Studio: recent
  Studio builds ship a JBR 25, and Gradle 8.9 fails on it with a bare version
  string as its entire error message (`* What went wrong: 25.0.2`). Point
  `JAVA_HOME` at a 21 if that happens.
- **Python 3.9+** with Pillow, for the corpus builder.
- About **500 MB free** on the device.

## Enabling adb

1. Settings → About device → tap **Build number** seven times.
2. Settings → Developer options → **USB debugging**.
3. Plug in over USB and accept the RSA prompt.

```bash
adb devices -l
# VB5221AU20249    device product:V2 model:V2 device:V2
```

Sunmi also ships a `com.sunmi.adb` helper; if the device does not appear, check
that it has not disabled debugging on its own.

## Confirming the hardware

Worth doing before building anything, since these numbers drive the design:

```bash
adb shell "getprop ro.build.version.release; getprop ro.product.model; getprop ro.product.cpu.abilist"
adb shell "cat /proc/meminfo | head -1; df -h /data"
adb shell "pm list packages | grep woyou"
```

A stock V2 answers roughly:

```
7.1.1
V2
armeabi-v7a,armeabi
MemTotal:  909844 kB
/dev/block/dm-1  3.8G  1.9G  1.8G  52%  /data
package:woyou.aidlservice.jiuiv5
```

That last line is the one that matters. No printer service, no printing.

## Building and installing

```bash
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Gradle needs to know where the SDK is. Either set `ANDROID_HOME`, or create
`local.properties` in the repository root:

```properties
sdk.dir=C:\\Users\\you\\AppData\\Local\\Android\\Sdk
```

> On Windows, write that file **without a BOM**. PowerShell's
> `Set-Content -Encoding utf8` adds one, and Gradle then fails with "SDK location
> not found" while showing you a file that looks perfectly correct.

## Pushing the corpus

```bash
cd tools/momirdeck
python momirdeck.py push
```

That is `adb push` to `/sdcard/Android/data/io.github.moobaloo.momir.dev.debug/files/`. An
app always owns its external files directory, so no runtime permission is
involved. Pushing the 427 MB corpus takes about half a minute over USB 2.

The app picks the files up on next launch. To do it by hand:

```bash
adb shell mkdir -p /sdcard/Android/data/io.github.moobaloo.momir.dev.debug/files
adb push out/momir.db  /sdcard/Android/data/io.github.moobaloo.momir.dev.debug/files/
adb push out/art.pack  /sdcard/Android/data/io.github.moobaloo.momir.dev.debug/files/
```

## Calibrating the paper geometry

Settings → **Test print** produces a slip with instructions on it. Tear it off
and measure the white band above the card name: that band *is* the distance from
the print head to the tear bar, and it is what **head to tear bar** should be set
to. The default is 12 mm.

- The band measures **more** than the setting → increase the setting.
- The last line is **cut off by the tear bar** → the setting is too high.

**Margin at the foot** is free choice: it is white paper under the rules text,
fed on top of the distance above, and it comes out of the layout's space rather
than the slip's length. The default is 5 mm.

Once the numbers agree, check that a slip slides into a sleeve. That is the real
test.

## Troubleshooting

**"No card data on this device".** The corpus is not where the app looks.

```bash
adb shell ls -la /sdcard/Android/data/io.github.moobaloo.momir.dev.debug/files/
```

**Nothing prints and no error appears.** Check that the service is bound:

```bash
adb logcat -s SunmiPrinter
# I/SunmiPrinter: Printer service connected
```

If it never connects, confirm `woyou.aidlservice.jiuiv5` is installed and has
not been disabled.

**The printer runs but the paper is blank.** The roll is in upside down.
Thermal paper only takes heat on one side.

**Slips are far too long.** Check that "slip length" is 88 mm and that "head to
tear bar" is not set to something like 30 mm.

**Resync fails.** The device needs to reach `api.scryfall.com` and
`data.scryfall.io` over HTTPS. Android 7.1 supports TLS 1.2, so this is normally
a WiFi problem.

```bash
adb logcat -s ScryfallSync
```

**The camera scanner will not open.** The app asks for `CAMERA` at runtime the
first time. If it was denied:

```bash
adb shell pm grant io.github.moobaloo.momir.dev.debug android.permission.CAMERA
```

## Development conveniences

Render a slip without printing it:

```bash
# long-press PRINT on the device, then
adb pull /sdcard/Android/data/io.github.moobaloo.momir.dev.debug/files/preview.png
```

Screenshot the app:

```bash
adb shell screencap -p /sdcard/s.png && adb pull /sdcard/s.png
```

> `screencap` returns an all-black image while the screen is off. Wake it first
> with `adb shell input keyevent KEYCODE_WAKEUP`. The activity is running and
> logcat looks healthy meanwhile, which makes this a confusing few minutes.

Watch everything the app says:

```bash
adb logcat -s SunmiPrinter:V ScryfallSync:V CardRepository:V ArtPack:V ScannerActivity:V
```
