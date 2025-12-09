# EKS Infrastructure Setup Guide

This guide explains how to use `infrastructure.tf` to provision an EKS cluster with worker nodes.

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

# Install Flux CLI (optional, for GitOps)
brew install fluxcd/tap/flux
```

### 2. Configure AWS Credentials

```bash
aws configure
```

Enter:
- AWS Access Key ID
- AWS Secret Access Key
- Default region: `us-west-2`
- Output format: `json`

Verify:
```bash
aws sts get-caller-identity
```

---

## Infrastructure Overview

The `infrastructure.tf` file creates:

| Resource | Description |
|----------|-------------|
| VPC | 10.0.0.0/16 CIDR block |
| Subnets | 2 public subnets across availability zones |
| Internet Gateway | For public internet access |
| Route Tables | Public routing configuration |
| EKS Cluster | Kubernetes control plane (v1.31) |
| Cluster IAM Role | Permissions for EKS service |
| Node Group | 2 x c7i-flex.large worker nodes |
| Node IAM Role | Permissions for EC2 worker nodes |

### IAM Policies

#### Cluster Role Policies
- AmazonEKSClusterPolicy
- AmazonEKSServicePolicy
- AmazonEKSVPCResourceController

#### Node Role Policies
- AmazonEKSWorkerNodePolicy
- AmazonEKS_CNI_Policy
- AmazonEC2ContainerRegistryReadOnly
- AmazonSSMManagedInstanceCore
- AmazonEC2FullAccess

---

## Deploy Infrastructure

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

### Step 5: Connect kubectl

After Terraform completes, connect to the cluster:

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
ip-10-0-1-xx.us-west-2.compute.internal    Ready    <none>   5m    v1.31.x
ip-10-0-2-xx.us-west-2.compute.internal    Ready    <none>   5m    v1.31.x
```

---

## Configuration Options

### Change Cluster Name

Edit `infrastructure.tf`:
```hcl
variable "cluster_name" {
  default = "your-cluster-name"
}
```

### Change Instance Type

Edit the node group section:
```hcl
instance_types = ["t3.large"]  # or your preferred type
```

### Change Node Count

Edit the scaling config:
```hcl
scaling_config {
  desired_size = 3  # number of nodes
  min_size     = 3
  max_size     = 3
}
```

### Change Region

Edit the provider:
```hcl
provider "aws" {
  region = "us-east-1"  # your region
}
```

---

## Common Commands

| Command | Description |
|---------|-------------|
| `terraform init` | Initialize Terraform |
| `terraform plan` | Preview changes |
| `terraform apply` | Apply changes |
| `terraform destroy` | Delete all resources |
| `terraform output` | Show output values |
| `terraform state list` | List managed resources |

---

## Check Status

### Cluster Status
```bash
aws eks describe-cluster --name elk-prod --region us-west-2 --query "cluster.status"
```

### Node Group Status
```bash
aws eks describe-nodegroup \
  --cluster-name elk-prod \
  --nodegroup-name elk-prod-nodes \
  --region us-west-2 \
  --query "nodegroup.status"
```

### EC2 Instances
```bash
aws ec2 describe-instances \
  --region us-west-2 \
  --filters "Name=tag:eks:cluster-name,Values=elk-prod" \
  --query "Reservations[].Instances[].{ID:InstanceId,State:State.Name}"
```

---

## Troubleshooting

### Terraform init fails
```bash
rm -rf .terraform .terraform.lock.hcl
terraform init
```

### State lock error
```bash
terraform force-unlock <LOCK_ID>
```

### No configuration files error
Ensure `infrastructure.tf` has no leading spaces on line 1.

### Cluster creation timeout
Check AWS Console: https://us-west-2.console.aws.amazon.com/eks

### Node group fails
- Check IAM role policies
- Verify instance type is available in your account
- Check subnet configuration

### kubectl connection refused
```bash
aws eks update-kubeconfig --name elk-prod --region us-west-2
```

### kubectl unauthorized
Add IAM access entry in EKS Console → Access tab.

---

## Cleanup

To destroy all resources:

```bash
terraform destroy
```

Type `yes` when prompted.

**Warning:** This deletes the entire cluster and all data.

---

## Cost Estimate

| Resource | Approximate Cost |
|----------|------------------|
| EKS Control Plane | $0.10/hour (~$73/month) |
| 2x c7i-flex.large | ~$0.08/hour each (~$115/month) |
| **Total** | **~$188/month** |

To reduce costs, destroy the cluster when not in use:
```bash
terraform destroy
```

---

## Next Steps

After infrastructure is ready:

1. **Create .env file**
   ```bash
   cp template.env .env
   # Edit with BRANCH and REPO_URL
   ```

2. **Deploy Flux**
   ```bash
   make flux-install
   make apply
   make reconcile
   ```

3. **Deploy ELK Stack**
   ```bash
   kubectl apply -f helm-repo/helm-repo.yaml
   kubectl apply -f elastic/eck-operator.yaml
   ```

4. **Access Kibana**
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
