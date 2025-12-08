#!/bin/bash

# ================================
# Create Filebeat AWS Credentials Secret
# ================================

NAMESPACE="elastic-system"
SECRET_NAME="filebeat-aws-creds"

echo "Creating Secret '$SECRET_NAME' in namespace '$NAMESPACE'..."

# Prompt for AWS credentials
read -p "Enter AWS_ACCESS_KEY_ID: " AWS_KEY
read -p "Enter AWS_SECRET_ACCESS_KEY: " AWS_SECRET
read -p "Enter AWS_REGION (default: us-west-2): " AWS_REGION

AWS_REGION=${AWS_REGION:-us-west-2}

kubectl -n $NAMESPACE create secret generic $SECRET_NAME \
  --from-literal=AWS_ACCESS_KEY_ID="$AWS_KEY" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$AWS_SECRET" \
  --from-literal=AWS_REGION="$AWS_REGION"

echo "Secret created successfully!"
echo "----------------------------------------"
kubectl -n $NAMESPACE get secret $SECRET_NAME
echo "----------------------------------------"
echo "Done."
