#!/usr/bin/env bash
# deploy.sh — create/update the patient-simulation host in your AWS account.
#
# RUN THIS ON YOUR OWN MACHINE from your VS Code terminal.
# It contains no credentials and uploads none: keys go separately via
# put_secrets.sh, straight into encrypted SSM.
#
# Usage:
#   ./deploy/aws/deploy.sh <stack-name> <region> <ec2-key-pair-name> [instance-type]
#
# Example:
#   ./deploy/aws/deploy.sh psim us-east-1 my-keypair
#
# Teardown (deletes the instance; SSM parameters are removed separately):
#   ./deploy/aws/deploy.sh <stack-name> <region> --delete

set -euo pipefail

STACK="${1:-}"
REGION="${2:-}"
KEYPAIR="${3:-}"
INSTANCE_TYPE="${4:-t3.small}"

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="${HERE}/cloudformation.yml"

if [ -z "$STACK" ] || [ -z "$REGION" ]; then
    echo "Usage: $0 <stack-name> <region> <ec2-key-pair-name> [instance-type]" >&2
    echo "       $0 <stack-name> <region> --delete" >&2
    exit 1
fi

command -v aws >/dev/null 2>&1 || { echo "ERROR: aws CLI not found." >&2; exit 2; }
aws sts get-caller-identity --region "$REGION" >/dev/null 2>&1 \
    || { echo "ERROR: AWS credentials not working for ${REGION}. Run 'aws configure'." >&2; exit 2; }

# ---- teardown -----------------------------------------------------------
if [ "$KEYPAIR" = "--delete" ]; then
    echo "Deleting stack ${STACK} in ${REGION}..."
    aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"
    echo "Stack deleted."
    echo ""
    echo "Your API keys are STILL in SSM (they outlive the stack by design, so"
    echo "you can redeploy without re-entering them). To remove them too:"
    for KEY in ANTHROPIC_API_KEY GROQ_API_KEY ELEVENLABS_API_KEY \
               LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do
        echo "  aws ssm delete-parameter --name /psim/${STACK}/${KEY} --region ${REGION}"
    done
    exit 0
fi

[ -z "$KEYPAIR" ] && { echo "ERROR: EC2 key pair name required." >&2; exit 1; }
[ -f "$TEMPLATE" ] || { echo "ERROR: template not found: ${TEMPLATE}" >&2; exit 2; }

# ---- lock SSH to just this machine --------------------------------------
echo "Detecting your public IP..."
MYIP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || echo '')"
if [ -z "$MYIP" ]; then
    echo "ERROR: could not detect your public IP. Pass it manually:" >&2
    echo "       MYIP_CIDR=1.2.3.4/32 $0 $STACK $REGION $KEYPAIR" >&2
    exit 2
fi
MYIP_CIDR="${MYIP_CIDR:-${MYIP}/32}"
echo "  SSH will be restricted to ${MYIP_CIDR}"

# ---- validate before spending anything ----------------------------------
echo "Validating template..."
aws cloudformation validate-template \
    --template-body "file://${TEMPLATE}" \
    --region "$REGION" >/dev/null
echo "  template OK"

# ---- deploy -------------------------------------------------------------
echo "Deploying stack ${STACK}..."
aws cloudformation deploy \
    --stack-name "$STACK" \
    --template-file "$TEMPLATE" \
    --region "$REGION" \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
        "KeyPairName=${KEYPAIR}" \
        "MyIpCidr=${MYIP_CIDR}" \
        "InstanceType=${INSTANCE_TYPE}" \
    2>/dev/null \
  || aws cloudformation deploy \
        --stack-name "$STACK" \
        --template-file "$TEMPLATE" \
        --region "$REGION" \
        --capabilities CAPABILITY_IAM \
        --no-fail-on-empty-changeset \
        --parameter-overrides \
            "KeyPairName=${KEYPAIR}" \
            "MyIpCidr=${MYIP_CIDR}" \
            "InstanceType=${INSTANCE_TYPE}"

echo ""
aws cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}' \
    --output table

INSTANCE_ID="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)"

cat <<EOF

  Bootstrap takes ~3-5 minutes (apt, venv, pip install). Watch it with:
    aws ssm start-session --target ${INSTANCE_ID} --region ${REGION}
    tail -f /var/log/psim-bootstrap.log     # 'BOOTSTRAP COMPLETE' when done

  NEXT STEP — load your keys from this machine (they never touch a chat):
    ./deploy/aws/put_secrets.sh ${STACK} ${REGION} .env
    ./deploy/aws/put_secrets.sh ${STACK} ${REGION} --check

  Then start the patient worker:
    aws ssm start-session --target ${INSTANCE_ID} --region ${REGION}
    sudo /usr/local/bin/psim-fetch-env
    sudo systemctl enable --now psim
    tail -f /opt/psim/app/logs/worker.log

EOF
