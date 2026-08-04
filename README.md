# AWS OIDC CI/CD Pipeline - Lab Guide

This is the lab I set up to learn how GitHub Actions talks to AWS **without storing AWS keys in GitHub**.

Push code to `main` → GitHub builds a Docker image → OIDC login to AWS → push image to ECR → SSH to EC2 → pull and run the container.

The app inside the container is a **tiny real LLM** (SmolLM2-135M). You can stream tokens in the browser after deploy.

---

## What you need before starting

Write these down on paper first - you'll use them everywhere:

- [ ] AWS account (root or admin user for setup)
- [ ] GitHub account + this repo cloned or forked
- [ ] AWS region I picked: `ap-south-1` (change if you use another)
- [ ] My ECR repo name: `my-app`
- [ ] My IAM role name: `github-actions-deploy`
- [ ] EC2 type: **t3.small** minimum (2 GB RAM - the LLM needs it)
- [ ] EC2 OS: **Ubuntu Server 22.04 or 24.04 LTS** (SSH user is `ubuntu`)

Get your AWS account ID:

```bash
aws sts get-caller-identity --query Account --output text
```

Example: `123456789012` - replace every `123456789012` in this lab with yours.

Get your GitHub repo path:

`YOUR_GITHUB_USER/aws-oidc-cicd-pipeline`

Example: `rajinikanthvadla-ai/aws-oidc-cicd-pipeline`

---

## Step-by-step - do exactly in this order

### Step 1 - Put the code on GitHub

```bash
git clone https://github.com/YOUR_GITHUB_USER/aws-oidc-cicd-pipeline.git
cd aws-oidc-cicd-pipeline
```

If you forked it, clone your fork. Make sure `main` branch exists.

---

### Step 2 - Create OIDC provider in AWS (one time only)

This tells AWS: "I trust tokens from GitHub Actions."

1. Open AWS Console → **IAM**
2. Left menu → **Identity providers**
3. Click **Add provider**
4. Choose **OpenID Connect**
5. Provider URL:

   `https://token.actions.githubusercontent.com`

6. Click **Get thumbprint** (let it fill automatically)
7. Audience:

   `sts.amazonaws.com`

8. Click **Add provider**

Done. You won't repeat this unless you delete it.

---

### Step 3 - Create IAM role for GitHub (OIDC role)

This role is what GitHub Actions will "become" when the workflow runs.

1. IAM → **Roles** → **Create role**
2. Trusted entity type: **Web identity**
3. Identity provider: `token.actions.githubusercontent.com`
4. Audience: `sts.amazonaws.com`
5. Click **Next**
6. Attach policy: search and tick **`AmazonEC2ContainerRegistryPowerUser`**
   - This lets the workflow push Docker images to ECR
7. Role name: `github-actions-deploy`
8. Create role

Now open that role → **Trust relationships** → **Edit trust policy**.

Replace the whole JSON with this (change **YOUR_ACCOUNT_ID** and **YOUR_GITHUB_USER/YOUR_REPO**):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:YOUR_GITHUB_USER/YOUR_REPO:*"
      }
    }
  }]
}
```

Save it.

Copy the **Role ARN** from the role summary page. Mine looks like:

`arn:aws:iam::123456789012:role/github-actions-deploy`

Keep this ARN — you paste it in the workflow file in Step 8.

---

### Step 4 - Create ECR repository

ECR = where Docker images live in AWS.

```bash
aws ecr create-repository --repository-name my-app --region ap-south-1
```

Note the URI from the output:

`123456789012.dkr.ecr.ap-south-1.amazonaws.com/my-app`

If repo already exists, that's fine — skip create.

---

### Step 5 - Launch EC2 instance (Ubuntu)

1. EC2 Console → **Launch instance**
2. Name: `llm-app-server` (anything works)
3. AMI: **Ubuntu Server 24.04 LTS** (or 22.04 LTS)
   - Quick filter in AMI search: type `ubuntu` -> pick **64-bit (x86)** official Canonical image
4. Instance type: **t3.small** (don't use t2.micro - OOM on LLM load)
5. Key pair: create new or pick existing — **download the .pem file**
6. Security group — allow these inbound:
   - SSH → port **22** → My IP (or your IP)
   - Custom TCP → port **3000** → Anywhere (or My IP for testing)
7. Launch

After it starts, copy the **Public IP**. Example: `3.110.xx.xx`

That's your `EC2_HOST`.

**Note:** Ubuntu AMI default login user is `ubuntu` (not `ec2-user`). The workflow already uses `ubuntu`.

---

### Step 6 - Give EC2 permission to pull from ECR

EC2 needs to pull the image after GitHub pushes it.

1. IAM → **Roles** → **Create role**
2. Trusted entity: **AWS service** → **EC2**
3. Attach policy: **`AmazonEC2ContainerRegistryReadOnly`**
4. Name: `ec2-ecr-read-role`
5. Create role

Go back to EC2 → your instance → **Actions** → **Security** → **Modify IAM role** → attach `ec2-ecr-read-role`.

---

### Step 7 - Install Docker + AWS CLI on Ubuntu EC2

SSH into the box (Ubuntu user is `ubuntu`):

```bash
ssh -i your-key.pem ubuntu@EC2_PUBLIC_IP
```

On the EC2 machine run:

```bash
sudo apt-get update
sudo apt-get install -y docker.io awscli
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ubuntu
```

`docker.io` — runs containers  
`awscli` — needed for `aws ecr get-login-password` on deploy (uses the EC2 IAM role from Step 6)

Log out and SSH back in (so `docker` group works):

```bash
exit
ssh -i your-key.pem ubuntu@EC2_PUBLIC_IP
```

Quick test:

```bash
docker ps
aws sts get-caller-identity
```

`docker ps` should not say "permission denied".  
`aws sts get-caller-identity` should show the EC2 instance role account (after Step 6 IAM role is attached).

---

### Step 8 - Edit the workflow file in your repo

Open `.github/workflows/deploy.yml` on your laptop.

Replace these placeholders everywhere they appear:

| Find this | Put your value |
|-----------|----------------|
| `123456789012` | your AWS account ID |
| `ap-south-1` | your region (if different) |
| `my-app` | your ECR repo name (if different) |
| `arn:aws:iam::123456789012:role/github-actions-deploy` | your full Role ARN from Step 3 |

The important lines look like this after you edit:

```yaml
env:
  AWS_REGION: ap-south-1
  ECR_REPO: my-app

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/github-actions-deploy
          aws-region: ${{ env.AWS_REGION }}
```

And in the SSH deploy script at the bottom — same account ID in the `docker login` line:

```bash
docker login --username AWS --password-stdin 123456789012.dkr.ecr.ap-south-1.amazonaws.com
```

Commit and push:

```bash
git add .github/workflows/deploy.yml
git commit -m "Configure AWS account and role for lab"
git push origin main
```

(Don't push yet if you haven't added secrets — do Step 9 first, then push.)

---

### Step 9 - Add GitHub Secrets (only 2 - no AWS keys!)

Repo on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret name | What to paste |
|-------------|----------------|
| `EC2_HOST` | EC2 public IP (e.g. `3.110.xx.xx`) |
| `EC2_SSH_KEY` | Open your `.pem` file in a text editor, copy **entire** contents including `-----BEGIN` and `-----END` lines |

**Important:** We do NOT add `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY`. OIDC handles AWS login for the workflow.

To get `.pem` content on Windows:

```bash
cat your-key.pem
```

Copy everything.

---

### Step 10 - Push to main and watch the pipeline

```bash
git push origin main
```

Go to GitHub → your repo → **Actions** tab.

You should see workflow **Build and Deploy** running.

What it does (watch the log):

1. Checkout code
2. **OIDC** — `configure-aws-credentials` gets temporary AWS creds (no keys in secrets)
3. Login to ECR
4. `docker build` (slow first time — downloads LLM into image, 10–15 min is normal)
5. `docker push` to ECR
6. SSH to EC2 → `docker pull` → stop old container → run new one

If something fails, jump to Troubleshooting at the bottom.

---

### Step 11 - Verify the app is running

Wait until GitHub Actions shows green.

On EC2 you can also check:

```bash
docker ps
```

You should see container name `my-app` on port 3000.

First start loads the model into RAM - give it 30-60 seconds after container starts.

**Browser:**

`http://EC2_PUBLIC_IP:3000/demo`

Type a prompt → click Generate → tokens should stream like ChatGPT (tiny model, so quality is basic but it's real inference).

**Health check:**

```bash
curl http://EC2_PUBLIC_IP:3000/health
```

**Single prompt (JSON):**

```bash
curl -s -X POST http://EC2_PUBLIC_IP:3000/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is OIDC in one line?","max_new_tokens":64}'
```

**Stream tokens in terminal:**

```bash
curl -N -X POST http://EC2_PUBLIC_IP:3000/v1/generate/stream \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Summarize CI/CD in one sentence.","max_new_tokens":80}'
```

**Batch (multiple prompts one shot):**

```bash
curl -s -X POST http://EC2_PUBLIC_IP:3000/v1/batch/infer \
  -H "Content-Type: application/json" \
  -d '{"items":[{"id":"1","prompt":"Hello"},{"id":"2","prompt":"What is Docker?"}]}'
```

API docs: `http://EC2_PUBLIC_IP:3000/docs`

---

## Where OIDC actually happens

OIDC is **only** in the GitHub Actions → AWS part. Not in the LLM app.

In `deploy.yml`:

```yaml
permissions:
  id-token: write    # GitHub mints a JWT for this run

- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::123456789012:role/github-actions-deploy
```

Flow:

```
GitHub Actions run starts
  -> GitHub gives a short-lived JWT (OIDC token)
  -> AWS STS checks: "Is this token from my repo?"
  -> If yes -> temporary AWS credentials (15 min to 1 hr)
  -> Workflow uses those creds to push to ECR
```

No long-lived AWS keys sitting in GitHub. That's the whole point of this lab.

EC2 deploy still uses SSH key (`EC2_SSH_KEY`). That's separate - not OIDC.

---

## Pipeline flow

```
git push main
    |
GitHub Actions
    |
OIDC JWT -> AWS IAM role -> temp credentials
    |
docker build (includes tiny LLM)
    |
docker push -> ECR
    |
SSH to EC2
    |
docker pull -> docker run
    |
http://EC2_IP:3000/demo  (stream tokens here)
```

---

## Files in this repo

```
.
├── app.py                      # FastAPI + SmolLM2 tiny LLM
├── requirements.txt
├── Dockerfile                  # builds image + downloads model
├── .github/workflows/deploy.yml  # OIDC + build + deploy
└── README.md                   # this lab guide
```

---

## Run locally on your laptop

If you want to test before AWS:

```bash
docker build -t my-app:local .
docker run -p 3000:3000 --memory=2g -e MODEL_VERSION=local my-app:local
```

First build takes a while (PyTorch + model download). Then open http://localhost:3000/demo

---

## Troubleshooting

| Problem | What I checked |
|---------|----------------|
| `Could not assume role` / OIDC error | Trust policy `sub` must be exactly `repo:USER/REPO:*` — no typo, case sensitive |
| Wrong role ARN in workflow | Copy ARN again from IAM role page |
| ECR push denied | Role has `AmazonEC2ContainerRegistryPowerUser` attached |
| SSH step failed | EC2_HOST correct? .pem pasted fully in EC2_SSH_KEY? Security group allows SSH from internet (GitHub runners are external) |
| docker pull failed on EC2 | EC2 IAM role has AmazonEC2ContainerRegistryReadOnly? Account ID in docker login URL correct? |
| App not loading in browser | Security group port 3000 open? docker ps shows my-app running? Wait 60s for model load |
| Container exits / OOM | Instance too small - use t3.small, workflow sets --memory=2g |
| Build step very slow | Normal - LLM + PyTorch in Docker image, first build 10-15 min |

---

## After the lab

What we built:

- ✅ GitHub Actions CI/CD on push to `main`
- ✅ AWS OIDC (no AWS keys in GitHub)
- ✅ ECR for images
- ✅ EC2 running a real tiny LLM with streaming API

For production I'd swap SSH deploy for ECS/EKS/CodeDeploy - but this lab is enough to understand OIDC + pipeline end to end.

---

Lab done when: Actions green + http://EC2_IP:3000/demo streams text + /health returns ok.
