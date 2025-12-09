# EKS Cluster & Node Group Setup Guide

This guide walks you through provisioning a complete EKS cluster with worker nodes for the `elk-prod` environment.

## Prerequisites

### 1. Install Required Tools

```bash
# Install AWS CLI
brew install awscli

# Install Terraform
brew tap hashicorp/tap
brew install hashicorp/tap/terraform

# Install kubectl
brew install kubectl

# Install Flux CLI
brew install fluxcd/tap/flux
```

### 2. Configure AWS Credentials

```bash
aws configure
```

Enter your:
- AWS Access Key ID
- AWS Secret Access Key
- Default region: `us-west-2`
- Output format: `json`

Verify configuration:
```bash
aws sts get-caller-identity
```

---

## Infrastructure Overview

The `infrastructure.tf` file provisions:

| Resource | Description |
|----------|-------------|
| VPC | 10.0.0.0/16 CIDR block |
| Subnets | 2 public subnets across AZs |
| Internet Gateway | For public internet access |
| Route Tables | Public routing |
| EKS Cluster | Kubernetes v1.28 |
| Node Group | 2 x t3.large (fixed, no autoscaling) |
| IAM Roles | Cluster and node roles with required policies |

---

## Deploy the Cluster

### Step 1: Navigate to Directory

```bash
cd clusters/elk-prod
```

### Step 2: Initialize Terraform

```bash
terraform init
```

Expected output:
```
Terraform has been successfully initialized!
```

### Step 3: Preview Changes

```bash
terraform plan
```

Review the resources that will be created.

### Step 4: Apply Configuration

```bash
terraform apply
```

Type `yes` when prompted.

**Note:** This takes approximately 15-20 minutes.

### Step 5: Configure kubectl

After Terraform completes, run the output command:

```bash
aws eks update-kubeconfig --name elk-prod --region us-west-2
```

### Step 6: Verify Cluster

```bash
kubectl get nodes
```

Expected output:
```
NAME                                       STATUS   ROLES    AGE   VERSION
ip-10-0-1-xx.us-west-2.compute.internal    Ready    <none>   5m    v1.28.x
ip-10-0-2-xx.us-west-2.compute.internal    Ready    <none>   5m    v1.28.x
```

---

## Cluster Configuration

### VPC
| Setting | Value |
|---------|-------|
| CIDR | 10.0.0.0/16 |
| Subnets | 10.0.1.0/24, 10.0.2.0/24 |
| Type | Public |

### EKS Cluster
| Setting | Value |
|---------|-------|
| Name | elk-prod |
| Version | 1.28 |
| Endpoint | Public + Private |
| Region | us-west-2 |

### Node Group
| Setting | Value |
|---------|-------|
| Instance Type | t3.large |
| Node Count | 2 (fixed) |
| Autoscaling | Disabled |

---

## Troubleshooting

### Terraform init fails

```bash
rm -rf .terraform .terraform.lock.hcl
terraform init
```

### Cluster creation timeout

Check AWS Console for status:
1. Go to EKS Console: https://us-west-2.console.aws.amazon.com/eks
2. Click on `elk-prod`
3. Check status

### Nodes not appearing

```bash
aws eks describe-nodegroup \
  --cluster-name elk-prod \
  --nodegroup-name elk-prod-nodes \
  --region us-west-2 \
  --query "nodegroup.status"
```

### kubectl connection issues

```bash
# Re-run kubeconfig update
aws eks update-kubeconfig --name elk-prod --region us-west-2

# Verify context
kubectl config current-context
```

### IAM permission errors

Ensure your AWS user has these permissions:
- `eks:*`
- `ec2:*`
- `iam:*`

---

## Cleanup

To destroy all resources:

```bash
terraform destroy
```

Type `yes` when prompted.

**Warning:** This deletes the entire cluster and all data.

---

## Next Steps

After the cluster is ready, deploy the ELK stack:

### 1. Create .env File

```bash
cp template.env .env
```

Edit `.env`:
```
BRANCH=main
REPO_URL=https://github.com/chelseajaculina/Multi-Agent-Collaboration-System
```

### 2. Deploy Flux

```bash
make flux-install
make apply
make reconcile
```

### 3. Deploy ELK Components

```bash
kubectl apply -f helm-repo/helm-repo.yaml
kubectl apply -f elastic/eck-operator.yaml
```

### 4. Access Kibana

```bash
kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system
```

Open: https://localhost:5601

Username: `elastic`

Get password:
```bash
kubectl -n elastic-system get secret elasticsearch-sample-es-elastic-user \
  -o go-template='{{.data.elastic | base64decode}}{{"\n"}}'
```

---

## Cost Estimate

| Resource | Approximate Cost |
|----------|------------------|
| EKS Control Plane | $0.10/hour (~$73/month) |
| 2x t3.large nodes | $0.0832/hour each (~$120/month) |
| **Total** | **~$193/month** |

To reduce costs, destroy the cluster when not in use:
```bash
terraform destroy
```
