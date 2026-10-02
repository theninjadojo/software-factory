#!/bin/sh
# Runs inside the sandbox. No network except a unix socket to the host allowlist proxy.
# /work holds one git repository per directory (a multi-repo project).
# AGENT_COMMAND is set by the orchestrator from the selected harness's configuration (never from ticket text) and
# reads the task from /task/prompt.txt; $MODEL and $MAX_TURNS are available to it.
socat TCP-LISTEN:3128,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/run/proxy.sock &
export HTTPS_PROXY=http://127.0.0.1:3128 HTTP_PROXY=http://127.0.0.1:3128
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 HOME=/home/agent
cd /work || exit 1
git config --global --add safe.directory '*'
if [ -z "$AGENT_COMMAND" ]; then
  AGENT_COMMAND='claude -p "$(cat /task/prompt.txt)" --model "$MODEL" --max-turns "$MAX_TURNS" --dangerously-skip-permissions'
fi
sh -c "$AGENT_COMMAND" > /out/agent.log 2>&1
echo $? > /out/exit_code
for d in /work/*/; do
  n=$(basename "$d")
  (cd "$d" && git add -A -N 2>/dev/null
   # A designer's mockups: collect them even if a .gitignore rule (for example `design/`, which matches at any depth) covers the
   # folder. Only this exact pattern is forced; the orchestrator validates every file before anything is published.
   if [ -n "$DESIGN_DIR" ]; then git add -f -N -- "$DESIGN_DIR"/factory-*.dc.html 2>/dev/null; fi
   git diff --binary > "/out/$n.diff" 2>/dev/null)
done
exit 0
