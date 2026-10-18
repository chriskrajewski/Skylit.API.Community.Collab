terraform {
  # 1.11: write-only arguments (value_wo on the secret parameters).
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Bucket comes from `terraform init -backend-config=backend.hcl`
  # (copy backend.hcl.example; the bootstrap stack prints the name).
  # use_lockfile is S3-native locking: no DynamoDB table.
  backend "s3" {
    key          = "infra/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
