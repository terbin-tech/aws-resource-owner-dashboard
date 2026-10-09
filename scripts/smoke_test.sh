#!/usr/bin/env bash
# Local smoke test after a deploy. Invokes the Lambda once (a full scan
# that rewrites site/index.html) and checks the page object's metadata, never its content.
# Prints only PASS or FAIL: <step>. Needs an admin session:
# `AWS_PROFILE=<admin profile> bash scripts/smoke_test.sh`.
set -u
cd "$(dirname "$0")/.."
fail() { echo "FAIL: $1"; rm -f out/smoke.json; exit 1; }

FN=$(terraform -chdir=infra/app output -raw function_name 2>/dev/null) || fail "terraform output"
BUCKET=$(terraform -chdir=infra/app output -raw report_bucket 2>/dev/null) || fail "terraform output"
install -d -m 700 out
START=$(date -u +%s)

# One attempt only, with a read timeout above the 600 s function timeout: a CLI retry
# would start a second full scan against LookupEvents' 2 req/s limit.
ERR=$(AWS_MAX_ATTEMPTS=1 aws lambda invoke --function-name "$FN" --region us-east-1 \
  --cli-read-timeout 900 --cli-binary-format raw-in-base64-out --payload '{}' \
  --query FunctionError --output text out/smoke.json 2>/dev/null) \
  || fail "invoke (the run may still finish: check the page's LastModified before rerunning)"
[ "$ERR" = "None" ] || fail "function error (see the log group)"
python3 -c 'import json, sys; sys.exit(json.load(open("out/smoke.json")) != {"ok": True})' \
  || fail "response is not {\"ok\": true}"
rm -f out/smoke.json

META=$(aws s3api head-object --bucket "$BUCKET" --key site/index.html --region us-east-1 \
  --query '[LastModified, ContentType]' --output text 2>/dev/null) || fail "head-object"
MODIFIED=${META%%$'\t'*}
TYPE=${META#*$'\t'}
MODIFIED_S=$(python3 -c 'import datetime, sys; print(int(datetime.datetime.fromisoformat(sys.argv[1]).timestamp()))' "$MODIFIED") \
  || fail "LastModified"
[ "$MODIFIED_S" -ge "$START" ] || fail "page not rewritten by this run"
[[ $TYPE == text/html* ]] || fail "content type"
echo PASS
