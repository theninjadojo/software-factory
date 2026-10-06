#!/bin/sh
# Verification recipe: build and test an iOS project with Xcode inside a throwaway macOS VM (Tart). docs/workers.md
#
# Runs on an Apple Silicon Mac. A prepared "golden" VM image holds Xcode and the simulators. For each job this script clones it
# (copy-on-write, so it is fast), boots the clone headless, copies the checkout in, runs `xcodebuild test`, copies a final simulator
# screenshot out, and deletes the clone. The agent-written project runs only inside that VM: no access to this Mac's files, keychain or
# signing identities, and nothing survives the job. Builds are for the simulator with code signing off: no identity is ever needed.
#
#   command = ["/path/to/ios-test.sh", "--scheme", "App", "--project", "App.xcodeproj"]
#   command = ["/path/to/ios-test.sh", "--scheme", "App", "--workspace", "App.xcworkspace", "--destination", "platform=iOS Simulator,name=iPhone 15"]
#   artifacts = ["build/screens/*.png"]
#
# Options: --scheme NAME (required)  --project PATH | --workspace PATH  --destination SPEC  --dir SUBFOLDER  --image VM_NAME  --boot-timeout SECONDS
# Exit 0 = passed; 1 = the build or tests failed (a failure: gets a fix round); 2 = the environment is wrong (no tart, no golden image,
# the VM did not boot or answer, bad options), which is never the patch's fault and is never retried.
# One worker per Mac: leftover shikumi-job-* VMs from a crashed run are deleted when the next job starts. Apple's licence allows two macOS
# VMs at a time on one Mac; this recipe uses one.
set -u
PREFLIGHT=0
IMAGE="shikumi-ios"; SCHEME=""; PROJECT=""; WORKSPACE=""; DEST="platform=iOS Simulator,name=iPhone 15"; DIR="."; BOOT_TIMEOUT=300
while [ $# -gt 0 ]; do
  case "$1" in
    --scheme) SCHEME="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --destination) DEST="${2:-}"; shift 2 ;;
    --dir) DIR="${2:-}"; shift 2 ;;
    --image) IMAGE="${2:-}"; shift 2 ;;
    --boot-timeout) BOOT_TIMEOUT="${2:-}"; shift 2 ;;
    --preflight) PREFLIGHT=1; shift ;;
    *) echo "[ios-test] unknown argument: $1" >&2; exit 2 ;;
  esac
done
die() { echo "[ios-test] $*" >&2; exit 2; }
if [ "$PREFLIGHT" = 1 ]; then       # the worker's question before it takes jobs: one line per thing missing, exit 3 when any
  bad=0
  command -v tart >/dev/null 2>&1 || { echo "tart is not installed (brew install cirruslabs/cli/tart)"; exit 3; }
  tart list 2>/dev/null | awk '{print $2}' | grep -qx "$IMAGE" || { echo "no Tart VM named $IMAGE yet: create it once (docs/workers.md, iOS golden image)"; bad=3; }
  exit $bad
fi
# Everything below ends up inside a shell command line in the VM, so only plain characters are allowed (no quotes, $, ; or backticks).
case "$SCHEME" in ""|*[!A-Za-z0-9_.-]*) die "--scheme is required and may contain letters, digits, dot, dash and underscore" ;; esac
for v in "$PROJECT" "$WORKSPACE"; do
  case "$v" in *[!A-Za-z0-9_./-]*|/*|*..*) die "--project and --workspace must be plain relative paths" ;; esac
done
[ -z "$PROJECT" ] || [ -z "$WORKSPACE" ] || die "give --project or --workspace, not both"
[ -n "$PROJECT$WORKSPACE" ] || die "give --project (a .xcodeproj) or --workspace (a .xcworkspace)"
NL='
'
case "$DEST" in ""|*"$NL"*) die "--destination may contain letters, digits, = , . _ - and spaces only" ;; esac
printf %s "$DEST" | grep -Eq '^[A-Za-z0-9=,._ -]+$' || die "--destination may contain letters, digits, = , . _ - and spaces only"
case "$IMAGE" in ""|*[!A-Za-z0-9._-]*) die "--image must be a plain VM name" ;; esac
case "$DIR" in /*|*..*|"") die "--dir must be a plain relative folder" ;; esac
case "$BOOT_TIMEOUT" in ""|*[!0-9]*) die "--boot-timeout must be a whole number of seconds" ;; esac
command -v tart >/dev/null 2>&1 || die "tart is not installed (macOS on Apple Silicon only: brew install cirruslabs/cli/tart)"
[ -d "$DIR" ] || die "no folder $DIR in the checkout"
SRC="$(cd "$DIR" && pwd)"
tart list 2>/dev/null | awk '{print $2}' | grep -qx "$IMAGE" || die "no VM image named $IMAGE: create the golden image first (docs/workers.md, \"iOS golden image\")"

VM="shikumi-job-$(date +%s)-$$"
GUEST=/Users/admin/work
RUN_PID=""
cleanup() {
  tart stop "$VM" >/dev/null 2>&1
  [ -n "$RUN_PID" ] && kill "$RUN_PID" 2>/dev/null
  tart delete "$VM" >/dev/null 2>&1
  true
}
trap cleanup EXIT
trap 'exit 143' INT TERM       # so SIGTERM from the worker (timeout or cancel) runs the cleanup above

# a crashed or killed earlier job may have left a VM behind (the worker runs one job at a time)
for old in $(tart list 2>/dev/null | awk '{print $2}' | grep '^shikumi-job-'); do tart delete "$old" >/dev/null 2>&1; done

echo "[ios-test] == clone $IMAGE -> $VM"
tart clone "$IMAGE" "$VM" || die "could not clone the golden image $IMAGE (disk full?)"
echo "[ios-test] == boot (headless)"
tart run --no-graphics "$VM" >/tmp/"$VM".log 2>&1 &
RUN_PID=$!
tart ip --wait "$BOOT_TIMEOUT" "$VM" >/dev/null 2>&1 || die "the VM did not get an address within ${BOOT_TIMEOUT}s: $(tail -2 /tmp/"$VM".log 2>/dev/null)"
waited=0
until tart exec "$VM" true >/dev/null 2>&1; do
  waited=$((waited + 5)); sleep "${BOOT_POLL:-5}"
  kill -0 "$RUN_PID" 2>/dev/null || die "the VM stopped while booting: $(tail -2 /tmp/"$VM".log 2>/dev/null)"
  [ "$waited" -lt "$BOOT_TIMEOUT" ] || die "the VM's guest agent did not answer within ${BOOT_TIMEOUT}s (does the golden image include it? docs/workers.md)"
done

echo "[ios-test] == copy the checkout in"
tart exec "$VM" /bin/sh -c "rm -rf $GUEST && mkdir -p $GUEST" || die "could not prepare $GUEST in the VM"
( cd "$SRC" && tar -cf - --exclude=.git . ) | tart exec -i "$VM" /bin/sh -c "tar -xf - -C $GUEST" || die "could not copy the checkout into the VM"

if [ -n "$WORKSPACE" ]; then TARGET="-workspace $WORKSPACE"; else TARGET="-project $PROJECT"; fi
echo "[ios-test] == xcodebuild test ($SCHEME on $DEST)"
tart exec "$VM" /bin/sh -c "cd $GUEST && xcodebuild test $TARGET -scheme $SCHEME -destination '$DEST' -derivedDataPath /tmp/dd CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO"
code=$?

# the simulator is usually still booted after the tests: keep what it shows, which is most useful when they failed
mkdir -p "$SRC/build/screens"
tart exec "$VM" /bin/sh -c "xcrun simctl io booted screenshot /tmp/final.png >/dev/null 2>&1 && cat /tmp/final.png" > "$SRC/build/screens/final.png" 2>/dev/null
[ -s "$SRC/build/screens/final.png" ] || rm -f "$SRC/build/screens/final.png"

case "$code" in
  0) echo "[ios-test] all steps passed"; exit 0 ;;
  65) echo "[ios-test] FAILED: xcodebuild test (build or tests failed)" >&2; exit 1 ;;
  64|66|70|71|72|73) die "xcodebuild could not run (exit $code): is the scheme, project or destination right, and is Xcode installed in the image?" ;;
  *) echo "[ios-test] FAILED: xcodebuild exited $code" >&2; exit 1 ;;
esac
