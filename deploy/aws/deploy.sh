#!/usr/bin/env bash
# deploy.sh — create/update the patient-simulation host in your AWS account.
#
# RUN THIS ON YOUR OWN MACHINE from your VS Code terminal.
# It contains no credentials and uploads none: keys go separately via
# put_secrets.sh, straight into encrypted SSM.
#
# Usage:
#   ./deploy/aws/deploy.sh <stack-name> <region> <ec2-key-pair-name> [instance-type]
#   ./deploy/aws/deploy.sh <stack-name> <region> --delete
#
# Example:
#   ./deploy/aws/deploy.sh psim us-east-1 malli
#
# Overrides (rarely needed):
#   MYIP_CIDR=1.2.3.4/32   pin the SSH source instead of auto-detecting
#   VPC_ID=vpc-xxxx        use a specific VPC instead of the default
#   SUBNET_ID=subnet-xxxx  use a specific subnet
#   REPO_BRANCH=master     clone a different branch onto the instance

set -euo pipefail

STACK="${1:-}"
REGION="${2:-}"
KEYPAIR="${3:-}"
INSTANCE_TYPE="${4:-t3.small}"

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="${HERE}/cloudformation.yml"

usage() {
    echo "Usage: $0 <stack-name> <region> <ec2-key-pair-name> [instance-type]" >&2
    echo "       $0 <stack-name> <region> --delete" >&2
}

[ -z "$STACK" ] || [ -z "$REGION" ] && { usage; exit 1; }

# ---- preflight: tell the operator everything that is missing, at once ----
MISSING=""
command -v aws  >/dev/null 2>&1 || MISSING="${MISSING}\n  * AWS CLI v2 — https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
command -v curl >/dev/null 2>&1 || MISSING="${MISSING}\n  * curl"

if [ -n "$MISSING" ]; then
    echo "" >&2
    echo "ERROR: missing prerequisites:" >&2
    printf '%b\n' "$MISSING" >&2
    echo "" >&2
    echo "  macOS:  brew install awscli" >&2
    echo "  Linux:  curl -fsSL 'https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip' -o a.zip \\" >&2
    echo "          && unzip -q a.zip && sudo ./aws/install" >&2
    echo "  Windows: winget install Amazon.AWSCLI   (or use the MSI installer)" >&2
    echo "" >&2
    echo "  Then run 'aws configure' and re-run this script." >&2
    echo "" >&2
    exit 2
fi

if ! CALLER="$(aws sts get-caller-identity --region "$REGION" --output text --query 'Arn' 2>&1)"; then
    echo "" >&2
    echo "ERROR: AWS credentials are not working for region ${REGION}." >&2
    echo "       ${CALLER}" >&2
    echo "" >&2
    echo "  Fix with:  aws configure          (needs an access key id + secret)" >&2
    echo "         or:  export AWS_PROFILE=your-profile" >&2
    echo "" >&2
    exit 2
fi
echo "Authenticated as: ${CALLER}"

# ---- teardown -----------------------------------------------------------
if [ "$KEYPAIR" = "--delete" ]; then
    echo "Deleting stack ${STACK} in ${REGION}..."
    aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"
    echo "Stack deleted."
    echo ""
    echo "Your API keys are STILL in SSM (they outlive the stack by design, so a"
    echo "redeploy does not need them re-entered). To remove them too:"
    for KEY in ANTHROPIC_API_KEY GROQ_API_KEY ELEVENLABS_API_KEY \
               LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do
        echo "  aws ssm delete-parameter --name /psim/${STACK}/${KEY} --region ${REGION}"
    done
    exit 0
fi

[ -z "$KEYPAIR" ] && { usage; exit 1; }
[ -f "$TEMPLATE" ] || { echo "ERROR: template not found: ${TEMPLATE}" >&2; exit 2; }

# ---- key pair must exist, in THIS region --------------------------------
if ! aws ec2 describe-key-pairs --key-names "$KEYPAIR" --region "$REGION" >/dev/null 2>&1; then
    echo "" >&2
    echo "ERROR: EC2 key pair '${KEYPAIR}' not found in ${REGION}." >&2
    echo "       Key pairs are per-region. Available here:" >&2
    aws ec2 describe-key-pairs --region "$REGION" \
        --query 'KeyPairs[].KeyName' --output text 2>/dev/null \
        | tr '\t' '\n' | sed 's/^/         /' >&2 || true
    echo "" >&2
    echo "  Create one:  aws ec2 create-key-pair --key-name ${KEYPAIR} --region ${REGION} \\" >&2
    echo "                 --query KeyMaterial --output text > ~/.ssh/${KEYPAIR}.pem" >&2
    echo "               chmod 400 ~/.ssh/${KEYPAIR}.pem" >&2
    echo "" >&2
    exit 2
fi

# ---- VPC + subnet -------------------------------------------------------
# Egress rules are only valid on a VPC security group, so VpcId is required.
if [ -z "${VPC_ID:-}" ]; then
    VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" \
        --filters Name=is-default,Values=true \
        --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo 'None')"
fi
if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
    echo "ERROR: no default VPC in ${REGION}. Pass one explicitly:" >&2
    echo "       VPC_ID=vpc-xxxx SUBNET_ID=subnet-xxxx $0 $STACK $REGION $KEYPAIR" >&2
    exit 2
fi

if [ -z "${SUBNET_ID:-}" ]; then
    # Prefer a subnet that auto-assigns a public IP, so the box can reach
    # GitHub/apt/SSM without us provisioning a NAT gateway.
    SUBNET_ID="$(aws ec2 describe-subnets --region "$REGION" \
        --filters "Name=vpc-id,Values=${VPC_ID}" "Name=map-public-ip-on-launch,Values=true" \
        --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo 'None')"
fi
if [ -z "$SUBNET_ID" ] || [ "$SUBNET_ID" = "None" ]; then
    echo "ERROR: no public subnet found in ${VPC_ID}." >&2
    echo "       The instance needs outbound internet (GitHub, PyPI, SSM, the AI APIs)." >&2
    echo "       Pass one explicitly:  SUBNET_ID=subnet-xxxx $0 ..." >&2
    exit 2
fi
echo "Network: ${VPC_ID} / ${SUBNET_ID}"

# ---- lock SSH to just this machine --------------------------------------
if [ -z "${MYIP_CIDR:-}" ]; then
    echo "Detecting your public IP..."
    MYIP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || echo '')"
    [ -z "$MYIP" ] && MYIP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null | tr -d '[:space:]' || echo '')"
    if [ -z "$MYIP" ]; then
        echo "ERROR: could not detect your public IP. Pass it manually:" >&2
        echo "       MYIP_CIDR=1.2.3.4/32 $0 $STACK $REGION $KEYPAIR" >&2
        exit 2
    fi
    MYIP_CIDR="${MYIP}/32"
fi
echo "SSH will be restricted to ${MYIP_CIDR}"

# ---- validate before spending anything ----------------------------------
echo "Validating template..."
aws cloudformation validate-template \
    --template-body "file://${TEMPLATE}" \
    --region "$REGION" >/dev/null
echo "  template OK"

# ---- deploy -------------------------------------------------------------
echo "Deploying stack ${STACK} (first run takes a few minutes)..."
set +e
aws cloudformation deploy \
    --stack-name "$STACK" \
    --template-file "$TEMPLATE" \
    --region "$REGION" \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
        "KeyPairName=${KEYPAIR}" \
        "VpcId=${VPC_ID}" \
        "SubnetId=${SUBNET_ID}" \
        "MyIpCidr=${MYIP_CIDR}" \
        "InstanceType=${INSTANCE_TYPE}" \
        "RepoBranch=${REPO_BRANCH:-genspark_ai_developer}"
DEPLOY_RC=$?
set -e

if [ "$DEPLOY_RC" -ne 0 ]; then
    echo "" >&2
    echo "Deploy failed. Most recent stack errors:" >&2
    aws cloudformation describe-stack-events --stack-name "$STACK" --region "$REGION" \
        --query "StackEvents[?ResourceStatus=='CREATE_FAILED' || ResourceStatus=='UPDATE_FAILED'].{Resource:LogicalResourceId,Reason:ResourceStatusReason}" \
        --output table 2>/dev/null | head -30 >&2 || true
    exit "$DEPLOY_RC"
fi

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
    cp .env.example .env        # if you do not already have a .env here
    # fill it in, then:
    ./deploy/aws/put_secrets.sh ${STACK} ${REGION} .env
    ./deploy/aws/put_secrets.sh ${STACK} ${REGION} --check

  Then start the patient worker:
    aws ssm start-session --target ${INSTANCE_ID} --region ${REGION}
    sudo /usr/local/bin/psim-fetch-env
    sudo systemctl enable --now psim
    tail -f /opt/psim/app/logs/worker.log

EOF
