#!/usr/bin/env bash
# Masked summary of a Terraform log in out/. Prints the summary line, then each
# distinct error title with its AWS error code and a count. Never the snippets under an error
# (they quote terraform.tfvars), never ARNs, names in parentheses or 12-digit numbers.
# The full file stays in out/ for the user to read.
set -u
f=${1:?usage: tf_summary.sh out/<file>.txt}
[ -f "$f" ] || { echo "no such file"; exit 1; }
mask() { sed -E 's/[0-9]{12}/<account-id>/g; s#arn:[^ ]*#<arn>#g; s/"[^"]*"/"<name>"/g'; }
summary=$(sed -E 's/\x1b\[[0-9;]*m//g' "$f" | grep -E '^(Plan:|No changes\.|Apply complete!)' | mask)
errors=$(sed -E 's/\x1b\[[0-9;]*m//g' "$f" \
  | grep -E '^(│ )?Error: ' \
  | sed -E 's/^(│ )?Error: //' \
  | awk '{
      code = ""
      if (match($0, /api error [A-Za-z0-9.]+/)) code = " [" substr($0, RSTART + 10, RLENGTH - 10) "]"
      t = $0; sub(/[(:].*/, "", t); sub(/ +$/, "", t)
      print "Error: " substr(t, 1, 80) code
    }' \
  | mask | LC_ALL=C sort | uniq -c)
if [ -z "$summary" ] && [ -z "$errors" ]; then
  echo "no summary line found (read the file yourself)"
  exit 1
fi
[ -n "$summary" ] && printf '%s\n' "$summary"
[ -n "$errors" ] && printf '%s\n' "$errors"
exit 0
