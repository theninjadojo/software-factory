#!/bin/sh
# Runs inside the factory-android container, in /work (the checkout). Usage: android-run unit <gradle-task> | emulator <gradle-task>
# Exit codes: 0 passed, 1 the build or tests failed, 2 the environment or arguments are wrong (not the patch's fault).
set -u
MODE="${1:-}"; TASK="${2:-test}"
SDK="${ANDROID_HOME:-/opt/android-sdk}"
export ANDROID_HOME="$SDK" ANDROID_SDK_ROOT="$SDK"
export PATH="$PATH:$SDK/platform-tools:$SDK/emulator:$SDK/cmdline-tools/latest/bin"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-300}"
case "$TASK" in ""|*[!A-Za-z0-9:_-]*) echo "[android] bad gradle task name" >&2; exit 2 ;; esac
[ -f ./gradlew ] || { echo "[android] no gradlew in the project: this recipe needs the Gradle wrapper" >&2; exit 2; }
[ -x ./gradlew ] || chmod +x ./gradlew 2>/dev/null || true

EMU_PID=""
cleanup() { [ -n "$EMU_PID" ] && { kill "$EMU_PID" 2>/dev/null; adb kill-server >/dev/null 2>&1; }; true; }
trap cleanup EXIT INT TERM

case "$MODE" in
  unit)
    echo "[android] == gradlew $TASK"
    ./gradlew --no-daemon --console=plain "$TASK" || { echo "[android] FAILED: gradlew $TASK" >&2; exit 1; }
    ;;
  emulator)
    command -v emulator >/dev/null 2>&1 || { echo "[android] this image was built without the emulator (WITH_EMULATOR=1)" >&2; exit 2; }
    export ANDROID_USER_HOME="${ANDROID_USER_HOME:-/tmp/android-home}" ANDROID_AVD_HOME="${ANDROID_AVD_HOME:-/tmp/android-home/avd}"
    mkdir -p "$ANDROID_AVD_HOME"
    echo "[android] == create AVD"
    echo no | avdmanager create avd -n ci -k "${SYSTEM_IMAGE:-system-images;android-34;google_apis;x86_64}" --force >/dev/null \
      || { echo "[android] could not create the AVD (is the system image installed?)" >&2; exit 2; }
    echo "[android] == boot emulator"
    emulator -avd ci -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect >/tmp/emulator.log 2>&1 &
    EMU_PID=$!
    adb wait-for-device
    waited=0
    until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
      waited=$((waited + 5)); sleep "${BOOT_POLL:-5}"
      kill -0 "$EMU_PID" 2>/dev/null || { echo "[android] the emulator exited: $(tail -3 /tmp/emulator.log)" >&2; exit 2; }
      [ "$waited" -lt "$BOOT_TIMEOUT" ] || { echo "[android] the emulator did not boot within ${BOOT_TIMEOUT}s" >&2; exit 2; }
    done
    echo "[android] == gradlew $TASK"
    status=0
    ./gradlew --no-daemon --console=plain "$TASK" || status=1
    mkdir -p build/screens && adb exec-out screencap -p > build/screens/final.png 2>/dev/null || rm -f build/screens/final.png
    [ "$status" = 0 ] || { echo "[android] FAILED: gradlew $TASK" >&2; exit 1; }
    ;;
  *) echo "[android] usage: android-run unit|emulator <gradle-task>" >&2; exit 2 ;;
esac
echo "[android] all steps passed"
