# One-time bootstrap stack: Terraform state bucket and GitHub OIDC roles.
#
# State stays local (terraform.tfstate in this folder, gitignored) because this
# stack creates the bucket the main stack keeps its state in. To move it into
# that bucket later, uncomment the backend block below, set the bucket name and
# run `terraform init -migrate-state`.
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # backend "s3" {
  #   bucket       = "skylit-fse-tfstate-<account>-us-east-1"
  #   key          = "bootstrap/terraform.tfstate"
  #   region       = "us-east-1"
  #   use_lockfile = true
  #   encrypt      = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "fse"
      ManagedBy = "terraform"
    }
  }
}
