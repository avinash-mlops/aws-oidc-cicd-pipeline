# AWS Resource Cleanup Guide

Delete everything you created in this lab.

Replace YOUR_ACCOUNT_ID and YOUR_REGION with your values.

---

## 1. OIDC Provider

List OIDC providers:

```bash
aws iam list-open-id-connect-providers
```

Delete OIDC provider:

```bash
aws iam delete-open-id-connect-provider \
  --open-id-connect-provider-arn arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com
```

---

## 2. IAM Roles

### Role: github-actions-deploy

List roles:

```bash
aws iam list-roles | grep github-actions-deploy
```

Detach policies from role:

```bash
aws iam detach-role-policy \
  --role-name github-actions-deploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser
```

Delete role:

```bash
aws iam delete-role --role-name github-actions-deploy
```

### Role: ec2-ecr-read-role

Detach policy:

```bash
aws iam detach-role-policy \
  --role-name ec2-ecr-read-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
```

Delete role:

```bash
aws iam delete-role --role-name ec2-ecr-read-role
```

---

## 3. ECR Repository

List repositories:

```bash
aws ecr describe-repositories --region YOUR_REGION
```

Delete repository (deletes all images inside):

```bash
aws ecr delete-repository \
  --repository-name my-app \
  --region YOUR_REGION \
  --force
```

---

## 4. EC2 Instance

List instances:

```bash
aws ec2 describe-instances \
  --region YOUR_REGION \
  --filters "Name=instance-state-name,Values=running"
```

Stop instance (keeps it, you can restart):

```bash
aws ec2 stop-instances \
  --instance-ids i-xxxxxxxxxxxxx \
  --region YOUR_REGION
```

Terminate instance (deletes it):

```bash
aws ec2 terminate-instances \
  --instance-ids i-xxxxxxxxxxxxx \
  --region YOUR_REGION
```

Replace i-xxxxxxxxxxxxx with your instance ID.

---

## 5. Security Group

List security groups:

```bash
aws ec2 describe-security-groups \
  --region YOUR_REGION \
  --filters "Name=group-name,Values=launch-wizard*"
```

Delete security group (only if no EC2 uses it):

```bash
aws ec2 delete-security-group \
  --group-id sg-xxxxxxxxxxxxx \
  --region YOUR_REGION
```

---

## 6. Key Pair

List key pairs:

```bash
aws ec2 describe-key-pairs --region YOUR_REGION
```

Delete key pair (deletes from AWS, not your local .pem file):

```bash
aws ec2 delete-key-pair \
  --key-name your-key-name \
  --region YOUR_REGION
```

Keep your .pem file safe if you plan to reuse it later.

---

## Quick cleanup (all at once)

If you have jq installed:

```bash
# Get account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="ap-south-1"

# Delete OIDC provider
aws iam delete-open-id-connect-provider \
  --open-id-connect-provider-arn arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com 2>/dev/null || true

# Delete IAM roles
for role in github-actions-deploy ec2-ecr-read-role; do
  echo "Deleting role: $role"
  aws iam detach-role-policy --role-name $role --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser 2>/dev/null || true
  aws iam detach-role-policy --role-name $role --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly 2>/dev/null || true
  aws iam delete-role --role-name $role 2>/dev/null || true
done

# Delete ECR
aws ecr delete-repository --repository-name my-app --region $REGION --force 2>/dev/null || true

# Delete EC2 instances
INSTANCE_ID=$(aws ec2 describe-instances --region $REGION --filters "Name=tag:Name,Values=llm-app-server" --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null)
if [ "$INSTANCE_ID" != "None" ] && [ -n "$INSTANCE_ID" ]; then
  echo "Terminating EC2: $INSTANCE_ID"
  aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION
fi

echo "Cleanup commands sent. Check AWS Console to verify deletion."
```

---

## GitHub Cleanup

Remove repository secrets you added:

1. Repo Settings -> Secrets and variables -> Actions
2. Delete EC2_HOST secret
3. Delete EC2_SSH_KEY secret

Remove workflow file (optional):

```bash
git rm .github/workflows/deploy.yml
git commit -m "Remove CI/CD workflow"
git push
```

---

## Verify everything is gone

```bash
# Check OIDC providers
aws iam list-open-id-connect-providers

# Check IAM roles
aws iam list-roles | grep -E "github-actions|ec2-ecr"

# Check ECR repos
aws ecr describe-repositories --region YOUR_REGION

# Check EC2 instances
aws ec2 describe-instances \
  --region YOUR_REGION \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId'
```

All should return empty or "No matches found".
