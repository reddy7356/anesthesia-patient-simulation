# Running the patient simulation on your own AWS machine

This deploys one Ubuntu EC2 instance that runs the LiveKit patient worker,
with your API keys held in KMS-encrypted SSM parameters.

**The point of this setup: your keys go from your laptop straight to AWS.
They are never pasted into a chat, never committed, and never handled by
anyone else's sandbox.**

---

## Before you start

On your local machine (the one with VS Code and your `.env`):

1. **AWS CLI v2** — `aws --version` should print `aws-cli/2.x`
   ([install](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
2. **Credentials** — `aws configure`, then check with `aws sts get-caller-identity`
3. **Session Manager plugin** (optional but recommended — gives you a shell
   without opening SSH at all)
   ([install](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html))
4. **An EC2 key pair** in your target region — EC2 → Network & Security → Key Pairs

---

## Step 1 — clone the repo locally

```bash
git clone -b genspark_ai_developer \
  https://github.com/reddy7356/anesthesia-patient-simulation.git
cd anesthesia-patient-simulation
```

## Step 2 — create the instance

```bash
./deploy/aws/deploy.sh psim us-east-1 YOUR_KEYPAIR_NAME
```

This auto-detects your public IP and restricts SSH to **only** that address.
Bootstrap takes 3–5 minutes. No secrets are involved in this step.

## Step 3 — put your `.env` into encrypted SSM

This is the step you were asking about. Your `.env` stays on your machine —
the script reads it locally and writes each value into AWS SSM as an encrypted
`SecureString`. Nothing is displayed.

```bash
chmod 600 .env
./deploy/aws/put_secrets.sh psim us-east-1 .env
```

Output shows masked fingerprints only:

```
  ANTHROPIC_API_KEY      set  len=108  sha256:4f2a9c31
  GROQ_API_KEY           set  len=56   sha256:8b1de077
  ELEVENLABS_API_KEY     set  len=51   sha256:c30fa914
  LIVEKIT_URL            wss://your-sim.livekit.cloud
```

Verify any time:

```bash
./deploy/aws/put_secrets.sh psim us-east-1 --check
```

## Step 4 — start the patient

```bash
aws ssm start-session --target <instance-id> --region us-east-1

# on the instance:
sudo /usr/local/bin/psim-fetch-env     # pulls SSM -> /opt/psim/app/.env (0600)
sudo systemctl enable --now psim
tail -f /opt/psim/app/logs/worker.log
```

Test the text path first — it needs only the Anthropic key:

```bash
cd /opt/psim/app
.venv/bin/python -m tools.dryrun case_001
```

---

## How the credentials are protected

| Layer | Protection |
|---|---|
| In transit | TLS from your machine to the SSM API |
| At rest | `SecureString`, encrypted with the `aws/ssm` KMS key |
| On the instance | `.env` written `0600`, owned by `ubuntu` |
| IAM scope | Instance role can read **only** `/psim/<stack>/*` — no other parameters |
| user-data | Contains **no secrets** (EC2 stores it in plaintext), only the fetch script |
| CloudFormation | Template has **no secrets** — `cfn:GetTemplate` reveals nothing |
| systemd | Worker logs to a file, not the journal, to keep keys out of `journalctl` |
| Disk | EBS volume encrypted |
| Network | SSH locked to your IP; **no inbound** rule for the worker (it dials out) |
| Git | `.env` is gitignored, along with `.env.*` and `*.env.bak` |

---

## Rotating a key

```bash
# edit .env locally, then re-push (put-parameter --overwrite is idempotent)
./deploy/aws/put_secrets.sh psim us-east-1 .env

# on the instance
sudo /usr/local/bin/psim-fetch-env && sudo systemctl restart psim
```

No redeploy needed.

## Teardown

```bash
./deploy/aws/deploy.sh psim us-east-1 --delete
```

SSM parameters deliberately **outlive** the stack so you can redeploy without
re-entering keys. The command prints the exact `delete-parameter` lines if you
want them gone too.

---

## Two things to know

**`console` mode will not work on EC2.** It needs a local mic and speaker, and
a headless instance has neither. Use:
- `dev` mode (the systemd service) — audio rides over LiveKit, works fine
- `tools.dryrun` — typed, no audio at all
- `console` mode only on your laptop

**Cost.** A `t3.small` is roughly $15/month if left on, plus API usage. Stop it
when idle:

```bash
aws ec2 stop-instances --instance-ids <instance-id> --region us-east-1
```

SSM parameters are free at this volume.
