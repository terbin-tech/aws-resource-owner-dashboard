#!/usr/bin/env bash
# Least-privilege check for the dashboard-dev role. Read-only; prints only pass/fail, never group names.
# TOTP users: asks for your MFA code once (run it in a real terminal); the AWS CLI caches the session.
# Passkey users: run `aws login --profile <login profile>` first; the role uses that session.
set -u
P=dashboard-dev
ok=0

expect_ok() {
  if out=$("$@" 2>&1); then echo "PASS  $LABEL"; else echo "FAIL  $LABEL: $(echo "$out" | grep -oE '\(([A-Za-z.]+)\)' | head -1)"; ok=1; fi
}
expect_denied() {
  out=$("$@" 2>&1)
  if echo "$out" | grep -qE 'UnauthorizedOperation|AccessDenied'; then echo "PASS  $LABEL (denied)"
  else echo "FAIL  $LABEL: expected a denial, got: $(echo "$out" | grep -oE '\(([A-Za-z.]+)\)|DryRunOperation' | head -1)"; ok=1; fi
}

LABEL="assume role with MFA"; expect_ok aws sts get-caller-identity --profile $P --query Arn --output text
LABEL="read in eu-central-1"; expect_ok aws ec2 describe-instances --region eu-central-1 --max-items 1 --profile $P
LABEL="read in us-east-1"; expect_ok aws ec2 describe-instances --region us-east-1 --max-items 1 --profile $P
LABEL="read in eu-north-1 must fail"; expect_denied aws ec2 describe-instances --region eu-north-1 --max-items 1 --profile $P
LABEL="list S3 must fail"; expect_denied aws s3 ls --profile $P
LABEL="allocate Elastic IP (dry run) must fail"; expect_denied aws ec2 allocate-address --dry-run --region eu-central-1 --profile $P
LABEL="list IAM users"; expect_ok aws iam list-users --max-items 1 --profile $P --query 'length(Users)'

# Cohort groups: the values of cohort_groups in the gitignored terraform.tfvars, read without
# printing them. Comments are dropped first, so a commented-out map doesn't count.
TFVARS="$(dirname "$0")/terraform.tfvars"
mapfile -t COHORT_GROUPS < <(python3 - "$TFVARS" 2>/dev/null <<'PY'
import re
import sys

text = re.sub(r"#.*", "", open(sys.argv[1], encoding="utf-8").read())
block = re.search(r"cohort_groups\s*=\s*\{(.*?)\}", text, re.S)
for name in re.findall(r'=\s*"([^"]+)"', block[1] if block else ""):
    print(name)
PY
)
if [ ${#COHORT_GROUPS[@]} -eq 0 ]; then echo "FAIL  cohort groups: none found in terraform.tfvars"; ok=1; fi
for i in "${!COHORT_GROUPS[@]}"; do
  LABEL="get cohort group $((i + 1)) of ${#COHORT_GROUPS[@]}"
  expect_ok aws iam get-group --group-name "${COHORT_GROUPS[$i]}" --max-items 1 --profile $P --query 'length(Users)'
done
LABEL="get a group outside cohort_groups must fail"
expect_denied aws iam get-group --group-name resource-owner-dashboard-no-such-group --max-items 1 --profile $P

[ $ok -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
