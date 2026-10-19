#!/bin/sh
# Verification recipe: build and test an Android (Gradle) project inside a throwaway container. docs/workers.md
#
# The toolchain (JDK, Android SDK, optionally an emulator) lives in the factory-android image (sandbox/android/), not on the worker host,
# and the agent-written project only ever runs inside that container: read-only root, no capabilities, no host mounts except the checkout
# and a Gradle cache, a memory/CPU/pid limit. It does have a network, because Gradle must fetch dependencies.
#
#   command = ["/path/to/android-test.sh"]                              unit tests:      ./gradlew test
#   command = ["/path/to/android-test.sh", "--dir", "app", "--task", ":app:testDebugUnitTest"]
#   command = ["/path/to/android-test.sh", "--emulator", "--task", "connectedDebugAndroidTest"]   needs /dev/kvm (Linux) and an image built with WITH_EMULATOR=1
#   artifacts = ["build/screens/*.png"]                                 (a final emulator screenshot)
#
# --screens (the Screens board, recipe android-screens): the screenshots the tests write are returned, one per test, as screens-out/*.png
# at the checkout's top, also when tests fail. Without --task it records Roborazzi or Paparazzi, whichever the project uses (JVM tests: no
# emulator); with --emulator it runs connectedDebugAndroidTest and keeps what the tests saved as additional test output.
#   command = ["/path/to/android-test.sh", "--screens"]
#   artifacts = ["screens-out/*.png"]
#
# When the worker sets FACTORY_STEPS=1 on a --screens run (a demo run for a video, docs/videos.md) the recipe runs the instrumented tests on
# the emulator whatever --emulator and --task say (connectedDebugAndroidTest, unless --task names another connected...AndroidTest task),
# with the image's step recorder (sandbox/android/steps/), and returns one PNG per Espresso action of each test, plus its in-between
# frames, in screens-out/. It writes what they show into FACTORY_RESULTS_DIR in the files the worker reads for a Playwright demo run:
# steps.tsv (as worker/recipes/trace-steps.mjs), junit.xml (each test's outcome; the id is the test file from the repository root,
# then :: and the method) and e2e-shots.jsonl (which test took each PNG). FACTORY_TEST_FILES (one test file a line, from the repository
# root) limits the run to those test classes; a file outside --dir, or not under src/androidTest*, is left out (exit 2 when all are).
#
# Options: --image NAME  --engine docker|podman  --dir SUBFOLDER  --task GRADLE_TASK  --emulator  --screens  --cache DIR  --kvm-device PATH
#          --memory 6g  --cpus 4
# Exit 0 = passed; 1 = the build or tests failed (a failure: gets a fix round); 2 = the environment is wrong (no engine, no image, no
# gradlew, no /dev/kvm, emulator did not boot...), which is never the patch's fault and is never retried.
set -u
PREFLIGHT=0
IMAGE="factory-android:latest"; ENGINE=""; DIR="."; TASK=""; TASKSET=0; EMULATOR=0; SCREENS=""
CACHE="${HOME:-/tmp}/.cache/factory-android-gradle"; KVM=/dev/kvm; MEMORY=6g; CPUS=4
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="${2:-}"; shift 2 ;;
    --engine) ENGINE="${2:-}"; shift 2 ;;
    --dir) DIR="${2:-}"; shift 2 ;;
    --task) TASK="${2:-}"; TASKSET=1; shift 2 ;;
    --emulator) EMULATOR=1; shift ;;
    --screens) SCREENS=screens; shift ;;
    --cache) CACHE="${2:-}"; shift 2 ;;
    --kvm-device) KVM="${2:-}"; shift 2 ;;
    --memory) MEMORY="${2:-}"; shift 2 ;;
    --cpus) CPUS="${2:-}"; shift 2 ;;
    --preflight) PREFLIGHT=1; shift ;;
    *) echo "[android-test] unknown argument: $1" >&2; exit 2 ;;
  esac
done
die() { echo "[android-test] $*" >&2; exit 2; }
case "$DIR" in /*|*..*|"") die "--dir must be a plain relative folder" ;; esac
if [ "$TASKSET" = 0 ]; then
  TASK=test
  [ -n "$SCREENS" ] && { TASK=auto; [ "$EMULATOR" = 1 ] && TASK=connectedDebugAndroidTest; }
fi
STEP_FILES=""
if [ -n "$SCREENS" ] && [ "${FACTORY_STEPS:-}" = 1 ]; then      # a demo run: the step recorder needs the emulator
  SCREENS=steps; EMULATOR=1
  case "$TASK" in *connected*AndroidTest) ;; *) TASK=connectedDebugAndroidTest ;; esac
  if [ -n "${FACTORY_TEST_FILES:-}" ] && [ -f "$FACTORY_TEST_FILES" ]; then
    while IFS= read -r f; do              # the worker checked them; checked again, as they are passed on to the container
      case "$f" in ""|/*|*..*|*[!A-Za-z0-9_.@+/-]*) continue ;; esac
      if [ "$DIR" != . ]; then case "$f" in "$DIR"/*) f=${f#"$DIR"/} ;; *) continue ;; esac; fi
      STEP_FILES="${STEP_FILES:+$STEP_FILES }$f"
    done < "$FACTORY_TEST_FILES"
    [ -n "$STEP_FILES" ] || die "none of the chosen test files is in $DIR"
  fi
fi
case "$TASK" in ""|*[!A-Za-z0-9:_-]*) die "--task must be a Gradle task name such as test or :app:testDebugUnitTest" ;; esac
case "$IMAGE" in ""|*[!A-Za-z0-9._:/@-]*) die "--image has characters an image name cannot have" ;; esac
case "$MEMORY" in *[!0-9gGmM]*|"") die "--memory must look like 6g" ;; esac
case "$CPUS" in *[!0-9]*|"") die "--cpus must be a whole number" ;; esac
case "$CACHE" in /*) ;; *) die "--cache must be an absolute folder" ;; esac
if [ -z "$ENGINE" ]; then
  for e in docker podman; do command -v "$e" >/dev/null 2>&1 && { ENGINE="$e"; break; }; done
fi
case "$ENGINE" in docker|podman) ;; "") if [ "$PREFLIGHT" = 1 ]; then echo "neither docker nor podman is installed (the Android build runs in a container)"; exit 3; fi; die "neither docker nor podman is installed" ;; *) die "--engine must be docker or podman" ;; esac
command -v "$ENGINE" >/dev/null 2>&1 || die "$ENGINE is not installed"
if [ "$PREFLIGHT" = 1 ]; then       # the worker's question before it takes jobs: one line per thing missing, exit 3 when any
  bad=0
  "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1 || { echo "the image $IMAGE is not on this machine: build or pull it (see docs/workers.md, Android)"; bad=3; }
  exit $bad
fi
[ -d "$DIR" ] || die "no folder $DIR in the checkout"
[ -f "$DIR/gradlew" ] || die "no gradlew in $DIR: this recipe needs the project's Gradle wrapper"
ROOT="$(pwd)"; WORK="$(cd "$DIR" && pwd)"
mkdir -p "$CACHE" || die "cannot create the Gradle cache folder $CACHE"

MODE=unit; DEVICE=""; TMPSIZE=4g
if [ "$EMULATOR" = 1 ]; then
  MODE=emulator; TMPSIZE=16g          # the AVD lives in /tmp: the emulator refuses to boot without twice its 6 GB data partition free
                                      # there (a tmpfs only uses what is written, and that counts against --memory)
  [ -e "$KVM" ] || die "$KVM does not exist: emulator tests need KVM (Linux). Docker on a Mac cannot provide it"
  [ -r "$KVM" ] && [ -w "$KVM" ] || die "$KVM is not readable and writable by this user (add it to the kvm group)"
  DEVICE="--device $KVM"
fi

STEP_PREFIX=""; [ "$DIR" != . ] && STEP_PREFIX="${DIR%/}/"
rm -rf "$WORK/.factory-steps"
# /home/builder holds what Robolectric (Roborazzi, Paparazzi, many unit tests) downloads: the Android framework, a few hundred MB.
echo "[android-test] == $ENGINE run $IMAGE ($MODE: gradlew $TASK${SCREENS:+, keeping screenshots})"
# shellcheck disable=SC2086   # $DEVICE is either empty or one fixed "--device PATH" pair
"$ENGINE" run --rm --pull=never \
  --user "$(id -u):$(id -g)" \
  --read-only --cap-drop=all --security-opt=no-new-privileges --pids-limit=2048 --memory="$MEMORY" --cpus="$CPUS" \
  --tmpfs /tmp:rw,exec,size=$TMPSIZE --tmpfs /home/builder:rw,size=2g \
  -e HOME=/home/builder -e GRADLE_USER_HOME=/gradle-cache -e CI=true -e STEP_FILES="$STEP_FILES" -e STEP_PREFIX="$STEP_PREFIX" \
  -v "$WORK:/work:rw" -v "$CACHE:/gradle-cache:rw" $DEVICE \
  "$IMAGE" "$MODE" "$TASK" $SCREENS
code=$?
if [ "$SCREENS" = steps ]; then                 # what the steps show, out of the checkout for the worker: regular files only, never a link
  for f in steps.tsv junit.xml e2e-shots.jsonl; do
    src="$WORK/.factory-steps/$f"
    [ -n "${FACTORY_RESULTS_DIR:-}" ] && [ -f "$src" ] && [ ! -L "$src" ] && [ ! -L "$WORK/.factory-steps" ] && cp "$src" "$FACTORY_RESULTS_DIR/$f"
  done
  rm -rf "$WORK/.factory-steps"
fi
if [ -n "$SCREENS" ] && [ "$WORK" != "$ROOT" ] && [ -d "$WORK/screens-out" ]; then      # the worker collects them from the checkout's top
  mkdir -p "$ROOT/screens-out" && find "$WORK/screens-out" -maxdepth 1 -type f -name '*.png' -exec mv {} "$ROOT/screens-out/" \;
fi
case "$code" in
  0) echo "[android-test] all steps passed"; exit 0 ;;
  1) exit 1 ;;
  2) exit 2 ;;
  125|126|127) die "the container could not start (exit $code): is the image $IMAGE built? (docker build -t factory-android sandbox/android)" ;;
  *) echo "[android-test] the container exited $code" >&2; exit 1 ;;     # a crash or kill inside the build: report it as a failure
esac
