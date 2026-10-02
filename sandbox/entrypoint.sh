#!/bin/sh
# Runs inside the sandbox. No network except a unix socket to the host allowlist proxy.
# /work holds one git repository per directory (a multi-repo project).
socat TCP-LISTEN:3128,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/run/proxy.sock &
export HTTPS_PROXY=http://127.0.0.1:3128 HTTP_PROXY=http://127.0.0.1:3128
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 HOME=/home/agent
cd /work || exit 1
git config --global --add safe.directory '*'
claude -p "$(cat /task/prompt.txt)" --model "$MODEL" --max-turns "$MAX_TURNS" \
  --dangerously-skip-permissions > /out/agent.log 2>&1
echo $? > /out/exit_code
for d in /work/*/; do
  n=$(basename "$d")
  (cd "$d" && git add -A -N 2>/dev/null; git diff --binary > "/out/$n.diff" 2>/dev/null)
done
exit 0
