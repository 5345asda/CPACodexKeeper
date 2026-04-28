#!/usr/bin/env bash
# Apply the CPACodexKeeper entry button to a CLIProxyAPI management.html file.
#
# Usage:
#   ./apply.sh <path-to-management.html>
#
# The script reads the existing file, strips any prior injection (delimited by
# the BEGIN/END markers), and appends the script in inject.html before </body>.
# Idempotent — safe to re-run after upstream upgrades (provided you
# `disable-auto-update-panel: true` so upstream cannot overwrite it).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INJECT_FILE="$SCRIPT_DIR/inject.html"
BEGIN_MARK="<!-- BEGIN cpa-codex-keeper -->"
END_MARK="<!-- END cpa-codex-keeper -->"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <path-to-management.html>" >&2
  exit 64
fi

target="$1"
if [[ ! -f "$target" ]]; then
  echo "error: target file not found: $target" >&2
  echo >&2
  echo "CLIProxyAPI stores management.html in MANAGEMENT_STATIC_PATH, or by default" >&2
  echo "in a static/ directory next to the CLIProxyAPI config file." >&2
  echo "Open /management.html once so CLIProxyAPI downloads it, then locate it, for example:" >&2
  echo "  find /opt/homebrew/etc -name management.html -print 2>/dev/null" >&2
  echo "  docker compose exec <cliproxyapi-service> sh -lc 'find / -name management.html 2>/dev/null'" >&2
  exit 66
fi
if [[ ! -f "$INJECT_FILE" ]]; then
  echo "error: missing inject.html next to apply.sh" >&2
  exit 66
fi

backup="$target.bak.$(date +%s)"
cp "$target" "$backup"
echo "backup: $backup"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

python3 - "$target" "$INJECT_FILE" "$BEGIN_MARK" "$END_MARK" >"$tmp" <<'PY'
import re
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
inject_path = Path(sys.argv[2])
begin_mark = sys.argv[3]
end_mark = sys.argv[4]

content = target_path.read_text(encoding="utf-8")
inject = inject_path.read_text(encoding="utf-8").strip()

# Strip prior injection block, if any.
pattern = re.compile(re.escape(begin_mark) + r".*?" + re.escape(end_mark), re.DOTALL)
content = pattern.sub("", content)

block = f"\n{begin_mark}\n{inject}\n{end_mark}\n"

if "</body>" in content:
    content = content.replace("</body>", block + "</body>", 1)
elif "</html>" in content:
    content = content.replace("</html>", block + "</html>", 1)
else:
    content = content.rstrip() + block

sys.stdout.write(content)
PY

mode="$(python3 -c 'import os, stat, sys; print(format(stat.S_IMODE(os.stat(sys.argv[1]).st_mode), "o"))' "$target")"
chmod "$mode" "$tmp"
mv "$tmp" "$target"
trap - EXIT
echo "wrote: $target"
