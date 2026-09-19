#!/usr/bin/env bash
# t4 independent re-run harness: runs each acceptance script SEPARATELY,
# sequentially (no Chrome contention / no port races), capturing rc + raw output.
set -u
cd "$(dirname "$0")/.." || exit 1
OUT=.cache/t4-runs
mkdir -p "$OUT"
: > "$OUT/SUMMARY.txt"

run() {
  local name="$1"; shift
  echo "######################## $name" | tee -a "$OUT/SUMMARY.txt"
  echo "\$ $*" | tee -a "$OUT/SUMMARY.txt"
  { "$@" ; } > "$OUT/$name.log" 2>&1
  local rc=$?
  echo "EXIT=$rc" | tee -a "$OUT/SUMMARY.txt"
  tail -n 6 "$OUT/$name.log" | sed 's/^/    | /' | tee -a "$OUT/SUMMARY.txt"
  echo | tee -a "$OUT/SUMMARY.txt"
  return 0
}

run button-wrap        node scripts/check-button-wrap.mjs
run fullscreen-parity  node scripts/check-fullscreen-parity.mjs
run advanced-relocation node scripts/check-advanced-relocation.mjs
run topbar-fixes       node scripts/check-topbar-fixes.mjs
run webui-invariants-bare node scripts/webui-invariants.mjs
run webui-invariants-declared node scripts/webui-invariants.mjs --id-removed= --expect=html_lines=-1930,css_lines=-52,prompt_editor_append=-1
run api-contract       node scripts/audit-api-contract.mjs
run frontend-residue   node scripts/audit-frontend-residue.mjs
run wiki-badge         node scripts/verify-wiki-badge.mjs
run page-errors        node scripts/verify-page-errors.mjs
run inline-scope       node scripts/verify-inline-extraction-scope.mjs
run qa-loadorder       node services/webui/tests/qa_loadorder_check.mjs

echo "=== ALL DONE ===" | tee -a "$OUT/SUMMARY.txt"
