#!/bin/bash
# Smoke test: prove bwrap confinement semantics used by opencode-sandboxed-linux.sh
set -u
T=$(mktemp -d)
echo "taskdir: $T"
bwrap \
  --ro-bind / / \
  --dev-bind /dev /dev \
  --proc /proc \
  --bind "$T" "$T" \
  --die-with-parent \
  bash -c "echo inside > '$T/ok.txt' && echo TASKDIR_WRITE_OK; echo nope > \"\$HOME/blocked.txt\" 2>/dev/null && echo ESCAPE_BAD || echo HOME_WRITE_BLOCKED; echo nope > /tmp/blocked.txt 2>/dev/null && echo TMP_ESCAPE_BAD || echo TMP_WRITE_BLOCKED; curl -s -o /dev/null -w 'NET_%{http_code}\n' --max-time 10 https://openrouter.ai/api/v1/models | head -1"
cat "$T/ok.txt"
rm -rf "$T"
