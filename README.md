# AWS OIDC CI/CD Pipeline

Automated deployment pipeline: push to `main` triggers GitHub Actions to build a Docker image, authenticate to AWS via OIDC (no long-lived access keys), push the image to Amazon ECR, and deploy it on an EC2 instance over SSH.

## Architecture

```
Git push (main)
    → GitHub Actions
    → Docker build
    → OIDC → temporary AWS credentials
    → Push image to ECR
    → SSH to EC2 → docker pull → run container
```

| Component | Role |
|-----------|------|
| GitHub Actions | Builds the image and orchestrates deploy |
| OIDC + IAM | Issues short-lived AWS credentials scoped to this repository |
| Amazon ECR | Private Docker image registry |
| EC2 | Runs the application container |

The application (`app.py`) runs **SmolLM2-135M-Instruct** — a 135M-parameter instruct model — inside the container. It exposes REST endpoints for single completion, **token streaming** (real-time style), and batch inference. Open `http://EC2_HOST:3000/demo` in a browser to watch streamed tokens after deploy.

**EC2 sizing:** use at least **t3.small** (2 GB RAM). The Docker image includes the model weights; first container start loads the model into memory (~30–60s).

## Prerequisites

- GitHub repository with this code
- AWS account
- ECR repository for images
- EC2 instance with Docker installed and network access to ECR
- IAM OIDC identity provider and IAM role trusted by GitHub Actions

## AWS setup

### 1. OIDC identity provider

In **IAM → Identity providers → Add provider**:

| Field | Value |
|-------|-------|
| Type | OpenID Connect |
| Provider URL | `https://token.actions.githubusercontent.com` |
| Audience | `sts.amazonaws.com` |

### 2. IAM role for GitHub Actions

Create a role via **IAM → Roles → Create role → Web identity**:

- Identity provider: `token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

Trust policy (restrict to your repository):

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

Attach permissions required for this pipeline:

- `AmazonEC2ContainerRegistryPowerUser` — push and pull images in ECR
- Or a narrower custom policy scoped to your ECR repository ARN

Record the role ARN, for example:

`arn:aws:iam::123456789012:role/github-actions-deploy`

### 3. ECR repository

```bash
aws ecr create-repository --repository-name my-app --region ap-south-1
```

Image URI format:

`123456789012.dkr.ecr.ap-south-1.amazonaws.com/my-app`

### 4. EC2 instance

**Instance**

- Amazon Linux 2023 or Ubuntu
- Security group: allow inbound SSH (22) and application port (3000)

**Docker**

```bash
sudo yum update -y
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
```

Log out and back in so the `docker` group applies.

**ECR access**

Attach an IAM instance role with `AmazonEC2ContainerRegistryReadOnly`, or authenticate manually:

```bash
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin 123456789012.dkr.ecr.ap-south-1.amazonaws.com
```

## GitHub configuration

### Repository secrets

| Secret | Description |
|--------|-------------|
| `EC2_HOST` | EC2 public IP or hostname |
| `EC2_SSH_KEY` | Private key contents (`.pem`) for SSH deploy |

Do not store AWS access keys in GitHub Secrets. OIDC provides temporary credentials for the workflow.

### Workflow variables

Edit `.github/workflows/deploy.yml` and replace placeholders:

| Placeholder | Replace with |
|-------------|--------------|
| `123456789012` | Your AWS account ID |
| `ap-south-1` | Your AWS region |
| `my-app` | Your ECR repository name |
| `arn:aws:iam::123456789012:role/github-actions-deploy` | Your IAM role ARN |
| `ec2-user` | EC2 SSH user if not Amazon Linux |

Workflow environment defaults:

```yaml
env:
  AWS_REGION: ap-south-1
  ECR_REPO: my-app
  IMAGE_TAG: ${{ github.sha }}
```

Each commit is tagged with the Git SHA for traceability and rollback.

## Pipeline flow

| Step | Action |
|------|--------|
| 1 | Push to `main` |
| 2 | GitHub Actions starts the workflow |
| 3 | `configure-aws-credentials` exchanges the OIDC JWT for an AWS session |
| 4 | `docker build` creates the image on the runner |
| 5 | `docker push` uploads the image to ECR |
| 6 | SSH connects to EC2 and runs `docker pull` |
| 7 | Old container is stopped and removed; new container starts on port 3000 |
| 8 | Application available at `http://EC2_HOST:3000` |

## OIDC authentication

1. GitHub Actions requests a short-lived JWT for the workflow run.
2. AWS IAM validates the token against the configured OIDC provider and trust policy (`aud`, `sub`).
3. On success, AWS returns temporary credentials (typically 15 minutes to 1 hour).

No static AWS keys are stored in GitHub. Credentials are scoped to the repository defined in the IAM trust policy.

## Application API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness check |
| `GET /demo` | Browser UI with streaming output |
| `GET /docs` | OpenAPI interactive docs |
| `POST /v1/generate` | Single prompt → completion JSON |
| `POST /v1/generate/stream` | Single prompt → streamed plain-text tokens |
| `POST /v1/batch/infer` | Up to 16 prompts in one request |

**Single generation**

```bash
curl -s -X POST http://localhost:3000/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is OIDC in one line?","max_new_tokens":64}'
```

**Streaming (real-time tokens)**

```bash
curl -N -X POST http://localhost:3000/v1/generate/stream \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Summarize CI/CD in one sentence.","max_new_tokens":80}'
```

**Batch inference**

```bash
curl -s -X POST http://localhost:3000/v1/batch/infer \
  -H "Content-Type: application/json" \
  -d '{"items":[{"id":"1","prompt":"Hello"},{"id":"2","prompt":"What is Docker?"}]}'
```

Environment variables (set by workflow on deploy):

| Variable | Purpose |
|----------|---------|
| `MODEL_VERSION` | Git commit SHA served by this container |
| `MODEL_ID` | Hugging Face model id (default `HuggingFaceTB/SmolLM2-135M-Instruct`) |

## Project structure

```
.
├── app.py                        # FastAPI + tiny LLM inference
├── requirements.txt
├── Dockerfile
├── .github/workflows/deploy.yml
└── README.md
```

## Local development

Build and run locally (image build downloads the model; allow several minutes):

```bash
docker build -t my-app:local .
docker run -p 3000:3000 --memory=2g -e MODEL_VERSION=local my-app:local
```

Open `http://localhost:3000/demo` for streaming, or `http://localhost:3000/docs` for the API.

## Troubleshooting

| Issue | Check |
|-------|-------|
| OIDC / assume role fails | IAM trust policy `sub` matches `repo:OWNER/REPO:*`; role ARN is correct |
| ECR push denied | Role has ECR push permissions; repository name and region match workflow |
| SSH deploy fails | `EC2_HOST` and `EC2_SSH_KEY` secrets; security group allows SSH from GitHub runner IPs |
| Container not reachable | Security group allows port 3000; container is running (`docker ps`) |
| ECR pull fails on EC2 | Instance IAM role or `docker login` to ECR; region and registry URL match |

## Production considerations

This pipeline uses SSH and single-container deploy on EC2 for simplicity. For production workloads, consider ECS, EKS, AWS CodeDeploy, or another orchestration layer instead of direct SSH deploy.
