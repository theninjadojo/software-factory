#!/bin/sh
# Runs inside the factory-android container, in /work (the checkout). Usage: android-run unit|emulator <gradle-task> [screens]
# With "screens" (the Screens board) the screenshots the tests wrote are copied to /work/screens-out under unique names, also when tests
# fail. The task "auto" then means: record Roborazzi or Paparazzi screenshots, whichever the project uses (both run on the JVM: no emulator).
# Exit codes: 0 passed, 1 the build or tests failed, 2 the environment or arguments are wrong (not the patch's fault).
set -u
MODE="${1:-}"; TASK="${2:-test}"; SCREENS="${3:-}"
SDK="${ANDROID_HOME:-/opt/android-sdk}"
export ANDROID_HOME="$SDK" ANDROID_SDK_ROOT="$SDK"
export PATH="$PATH:$SDK/platform-tools:$SDK/emulator:$SDK/cmdline-tools/latest/bin"
# The container runs as the worker's uid, which has no passwd entry here, so Java would take "?" as the home folder and Robolectric
# (Roborazzi, many unit tests) could not create its download lock there. Every JVM Gradle starts, test workers too, reads this.
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-Duser.home=${HOME:-/home/builder}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-300}"
case "$TASK" in ""|*[!A-Za-z0-9:_-]*) echo "[android] bad gradle task name" >&2; exit 2 ;; esac
[ -f ./gradlew ] || { echo "[android] no gradlew in the project: this recipe needs the Gradle wrapper" >&2; exit 2; }
[ -x ./gradlew ] || chmod +x ./gradlew 2>/dev/null || true
case "$SCREENS" in ""|screens) ;; *) echo "[android] the third argument can only be screens" >&2; exit 2 ;; esac

uses() {        # whether a Gradle build file or version catalog of the project names this plugin
  find . \( -name build -o -name .gradle -o -name screens-out \) -prune -o -type f \( -name '*.gradle' -o -name '*.gradle.kts' -o -name '*.versions.toml' \) \
    -exec grep -li "$1" {} + 2>/dev/null | grep -q .
}
slug() { printf '%s' "$1" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9][^a-z0-9]*/-/g; s/^-//; s/-$//'; }
collect() {     # every screenshot the run wrote, under a readable unique name: module + test, with a checksum of the path
  mkdir -p screens-out
  find . -path ./screens-out -prune -o -type f -name '*.png' \( -path '*/build/outputs/roborazzi/*' -o -path '*/src/test/snapshots/images/*' \
    -o -path '*/build/outputs/connected_android_test_additional_output/*' \) -print | sort | while IFS= read -r f; do
    label=$(slug "$(printf '/%s' "${f#./}" | sed 's#/build/outputs/roborazzi/#/#; s#/src/test/snapshots/images/#/#; s#/build/outputs/connected_android_test_additional_output/#/#; s#\.png$##')")
    n=${#label}
    if [ "$n" -gt 46 ]; then label="$(printf '%s' "$label" | cut -c1-22)-$(printf '%s' "$label" | cut -c$((n - 22))-)"; fi   # keep both ends: the test is last
    cp "$f" "screens-out/${label:-shot}-$(printf '%s' "$f" | cksum | cut -d' ' -f1).png"
  done
  echo "[android] $(find screens-out -name '*.png' | wc -l) screenshot(s) kept"
}

if [ -n "$SCREENS" ]; then
  if [ "$TASK" = auto ]; then
    if uses roborazzi; then TASK=recordRoborazziDebug
    elif uses paparazzi; then TASK=recordPaparazziDebug
    else echo "[android] no Roborazzi or Paparazzi in this project: name a Gradle task with --task, or use --emulator" >&2; exit 2; fi
  fi
  find . -path '*/src/test/snapshots/images/*.png' -type f -delete 2>/dev/null    # Paparazzi's committed images: only this run's may show
fi

EMU_PID=""
cleanup() { [ -n "$EMU_PID" ] && { kill "$EMU_PID" 2>/dev/null; adb kill-server >/dev/null 2>&1; }; true; }
trap cleanup EXIT INT TERM

case "$MODE" in
  unit)
    echo "[android] == gradlew $TASK"
    status=0
    ./gradlew --no-daemon --console=plain "$TASK" || status=1
    [ -n "$SCREENS" ] && collect
    [ "$status" = 0 ] || { echo "[android] FAILED: gradlew $TASK" >&2; exit 1; }
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
    if [ -n "$SCREENS" ]; then
      collect
      if [ -z "$(find screens-out -name '*.png')" ] && [ -s build/screens/final.png ]; then cp build/screens/final.png screens-out/final.png; fi   # the tests saved none: the last screen at least
    fi
    [ "$status" = 0 ] || { echo "[android] FAILED: gradlew $TASK" >&2; exit 1; }
    ;;
  *) echo "[android] usage: android-run unit|emulator <gradle-task>" >&2; exit 2 ;;
esac
echo "[android] all steps passed"
