#!/bin/sh
# Runs inside the factory-android container, in /work (the checkout). Usage: android-run unit|emulator <gradle-task> [screens|steps]
# With "screens" (the Screens board) the screenshots the tests wrote are copied to /work/screens-out under unique names, also when tests
# fail. The task "auto" then means: record Roborazzi or Paparazzi screenshots, whichever the project uses (both run on the JVM: no emulator).
# With "steps" (emulator only: a demo run for a video, docs/videos.md) the tests run unchanged with the step recorder of /opt/shikumi-steps
# (StepListener.java) added to their sources, which keeps the screen after each Espresso action: the PNGs go to /work/screens-out and
# what they show to /work/.factory-steps (steps.tsv, junit.xml and e2e-shots.jsonl, the files the worker reads; android-test.sh moves
# them out of the checkout). STEP_FILES (space-separated test files, relative to /work) limits the run to those classes; STEP_PREFIX
# (the --dir folder and a slash, or empty) makes the test ids relative to the repository again.
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
case "$SCREENS" in ""|screens) ;; steps) [ "$MODE" = emulator ] || { echo "[android] steps need the emulator" >&2; exit 2; } ;;
  *) echo "[android] the third argument can only be screens or steps" >&2; exit 2 ;; esac
STEPS_SRC="${STEPS_SRC:-/opt/shikumi-steps/src}"; STEPS_TMP="${TMPDIR:-/tmp}"; STEPS_DEVICE=/data/local/tmp/shikumi-steps; STEP_FILES="${STEP_FILES:-}"; STEP_PREFIX="${STEP_PREFIX:-}"

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

# The class of an instrumented test file (its package line and its name); empty when it is not one under src/androidTest*.
test_class() {
  case "$1" in */src/androidTest*/*.kt|*/src/androidTest*/*.java|src/androidTest*/*.kt|src/androidTest*/*.java) ;; *) return 0 ;; esac
  [ -f "$1" ] && [ ! -L "$1" ] || return 0
  pkg=$(sed -n 's/^[[:space:]]*package[[:space:]][[:space:]]*\([A-Za-z0-9_.]*\).*/\1/p' "$1" | head -1)
  name=$(basename "$1"); name=${name%.*}
  printf '%s\n' "${pkg:+$pkg.}$name"
}
# The Gradle path of the module a file is in (:app for app/src/...; : for the root project).
module_of() { m=${1%%/src/androidTest*}; case "$1" in src/androidTest*) m="" ;; esac; printf ':%s\n' "$(printf '%s' "$m" | tr / :)"; }
# The file of a test class: a chosen file, else the one under src/androidTest* with its name and package.
file_of() {
  for f in $STEP_FILES; do [ "$(test_class "$f")" = "$1" ] && { printf '%s\n' "$f"; return; }; done
  simple=${1##*.}
  find . \( -name build -o -name .gradle -o -name screens-out \) -prune -o -type f \( -name "$simple.kt" -o -name "$simple.java" \) -path '*/src/androidTest*' -print 2>/dev/null \
    | sed 's#^\./##' | while IFS= read -r f; do [ "$(test_class "$f")" = "$1" ] && { printf '%s\n' "$f"; break; }; done
}
xml() { sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g'; }
# The step recorder's output (pulled to $1) as the worker reads a demo run: the PNGs in screens-out, and in .factory-steps the steps,
# a JUnit file with each test's outcome and file, and which test took each PNG; a test id is the file from the repository root, then
# :: and the method. Lines the recorder wrote wrong, or about a class with no file here, are left out.
steps_out() {
  mkdir -p screens-out .factory-steps
  rm -f .factory-steps/steps.tsv .factory-steps/junit.xml .factory-steps/e2e-shots.jsonl
  find "$1" -maxdepth 1 -type f -name '*.png' | while IFS= read -r f; do
    case "$(basename "$f")" in *[!a-z0-9.-]*|.*) continue ;; esac
    cp "$f" screens-out/
  done
  [ -f "$1/steps.tsv" ] && cp "$1/steps.tsv" .factory-steps/steps.tsv
  tab=$(printf '\t')
  { echo '<?xml version="1.0" encoding="UTF-8"?>'; echo '<testsuite name="android">'
    [ -f "$1/results.tsv" ] && while IFS="$tab" read -r cls meth outcome msg; do
      f=$(file_of "$cls"); [ -n "$f" ] || continue
      printf '<testcase file="%s" classname="" name="%s">' "$(printf '%s' "$STEP_PREFIX$f" | xml)" "$(printf '%s' "$meth" | xml)"
      [ "$outcome" = pass ] || printf '<failure message="%s"/>' "$(printf '%s' "$msg" | xml)"
      echo '</testcase>'
    done < "$1/results.tsv"
    echo '</testsuite>'; } > .factory-steps/junit.xml
  [ -f "$1/shots.tsv" ] && while IFS="$tab" read -r png cls meth; do
    f=$(file_of "$cls"); [ -n "$f" ] || continue
    case "$png$meth" in *[\\\"]*) continue ;; esac
    printf '{"file": "%s", "test": "%s::%s"}\n' "$png" "$STEP_PREFIX$f" "$meth"
  done < "$1/shots.tsv" > .factory-steps/e2e-shots.jsonl
  echo "[android] $(find screens-out -name '*.png' ! -name 'zz-*' | wc -l) step screenshot(s) and $(find screens-out -name 'zz-*.png' | wc -l) in-between frame(s) kept"
}

if [ "$SCREENS" = steps ]; then
  [ -d "$STEPS_SRC" ] || { echo "[android] this image has no step recorder ($STEPS_SRC)" >&2; exit 2; }
  CLASSES=""; MODULES=""
  for f in $STEP_FILES; do
    c=$(test_class "$f")
    [ -n "$c" ] || { echo "[android] $f is not an instrumented test (src/androidTest*/...kt or .java); left out" >&2; continue; }
    CLASSES="${CLASSES:+$CLASSES,}$c"
    m=$(module_of "$f"); case " $MODULES " in *" $m "*) ;; *) MODULES="$MODULES $m" ;; esac
  done
  if [ -n "$STEP_FILES" ] && [ -z "$CLASSES" ]; then echo "[android] none of the chosen test files is an instrumented test" >&2; exit 2; fi
  # Every Android module's instrumented tests get the recorder's source and run with it as a listener (after the project's own, if
  # it names one), limited to the chosen classes. Through the DSL, not -P: a runner argument from the command line breaks the
  # configuration cache. ($CLASSES holds only class-name characters: test_class reads [A-Za-z0-9_.] from the package line.)
  cat > "$STEPS_TMP/shikumi-steps.gradle" <<GRADLE
allprojects {
  ['com.android.application', 'com.android.library'].each { id ->
    pluginManager.withPlugin(id) {
      android.sourceSets.androidTest.java.srcDir('$STEPS_SRC')
      androidComponents.finalizeDsl { ext ->
        def a = ext.defaultConfig.testInstrumentationRunnerArguments
        a.put('listener', a.get('listener') ? a.get('listener') + ',shikumi.steps.StepListener' : 'shikumi.steps.StepListener')
        if ('$CLASSES') a.put('class', '$CLASSES')
      }
    }
  }
}
GRADLE
  STEP_ARGS="--init-script $STEPS_TMP/shikumi-steps.gradle"
  if [ -n "$CLASSES" ]; then          # only the modules of the chosen classes: another module's run would not find them
    T=""; for m in $MODULES; do [ "$m" = ":" ] && T="$T $TASK" || T="$T $m:$TASK"; done; TASK="${T# }"
  fi
fi

if [ -n "$SCREENS" ] && [ "$SCREENS" != steps ]; then
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
    DEVICE_ARGS=""; [ "$SCREENS" = steps ] && DEVICE_ARGS="-d ${AVD_DEVICE:-pixel_6}"      # a demo shows a phone-sized screen (1080 x 2400)
    # shellcheck disable=SC2086
    echo no | avdmanager create avd -n ci -k "${SYSTEM_IMAGE:-system-images;android-34;google_apis;x86_64}" $DEVICE_ARGS --force >/dev/null \
      || { echo "[android] could not create the AVD (is the system image installed?)" >&2; exit 2; }
    echo "[android] == boot emulator"
    emulator -avd ci -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect >/tmp/emulator.log 2>&1 &
    EMU_PID=$!
    waited=0
    until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do      # (adb wait-for-device would wait forever if it died)
      waited=$((waited + 5)); sleep "${BOOT_POLL:-5}"
      kill -0 "$EMU_PID" 2>/dev/null || { echo "[android] the emulator exited: $(tail -3 /tmp/emulator.log)" >&2; exit 2; }
      [ "$waited" -lt "$BOOT_TIMEOUT" ] || { echo "[android] the emulator did not boot within ${BOOT_TIMEOUT}s" >&2; exit 2; }
    done
    [ "$SCREENS" = steps ] && { adb shell "rm -rf $STEPS_DEVICE; mkdir -p $STEPS_DEVICE" || { echo "[android] could not prepare $STEPS_DEVICE" >&2; exit 2; }; }
    echo "[android] == gradlew $TASK${STEP_ARGS:+ (recording steps)}"
    status=0
    # shellcheck disable=SC2086   # $TASK may be several module tasks; $STEP_ARGS holds plain words
    ./gradlew --no-daemon --console=plain ${STEP_ARGS:-} $TASK || status=1
    mkdir -p build/screens && adb exec-out screencap -p > build/screens/final.png 2>/dev/null || rm -f build/screens/final.png
    if [ "$SCREENS" = steps ]; then
      rm -rf "$STEPS_TMP/steps-pulled" && mkdir -p "$STEPS_TMP/steps-pulled" && adb pull "$STEPS_DEVICE/." "$STEPS_TMP/steps-pulled" >/dev/null 2>&1
      adb logcat -d -s shikumi-steps:D 2>/dev/null | tail -n 60 | sed 's/^/[recorder] /'      # what it kept, or why it kept none
      steps_out "$STEPS_TMP/steps-pulled"
    elif [ -n "$SCREENS" ]; then
      collect
      if [ -z "$(find screens-out -name '*.png')" ] && [ -s build/screens/final.png ]; then cp build/screens/final.png screens-out/final.png; fi   # the tests saved none: the last screen at least
    fi
    [ "$status" = 0 ] || { echo "[android] FAILED: gradlew $TASK" >&2; exit 1; }
    ;;
  *) echo "[android] usage: android-run unit|emulator <gradle-task>" >&2; exit 2 ;;
esac
echo "[android] all steps passed"
