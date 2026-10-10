#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_dir="${ANDROID_HOME:-${ANDROID_SDK_ROOT:?Set ANDROID_HOME to the Android SDK}}"
test_dir="$(mktemp -d)"
trap 'rm -rf -- "$test_dir"' EXIT
# Use only the NDK JNI header with host libc; do not mix Android sysroot headers.
cp "$sdk_dir/ndk/26.1.10909125/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/jni.h" "$test_dir/jni.h"
cc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror -fsanitize=undefined -fno-sanitize-recover=all \
  -I"$test_dir" \
  "$repo_dir/tools/native/serial_test.c" -o "$test_dir/serial_test"
"$test_dir/serial_test"
echo 'Native UART boundary tests passed (no printer hardware accessed).'
