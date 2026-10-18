variable "region" {
  description = "AWS region for the state bucket and the roles' resource ARNs."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "GitHub owner/name allowed to assume the CI roles. IAM matches it case-sensitively."
  type        = string
  default     = "OWNER/REPO"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be owner/name."
  }
}

variable "github_immutable_subject_prefix" {
  description = "Immutable OIDC subject prefix of the repository (gh api repos/<owner>/<name>/actions/oidc/customization/sub, field sub_claim_prefix): repo:<owner>@<owner id>/<name>@<repo id>. Trusted next to the legacy repo:<owner>/<name> form. null: legacy form only."
  type        = string
  default     = "repo:OWNER@0/REPO@0"

  validation {
    condition     = var.github_immutable_subject_prefix == null || can(regex("^repo:[A-Za-z0-9_.-]+@[0-9]+/[A-Za-z0-9_.-]+@[0-9]+$", var.github_immutable_subject_prefix))
    error_message = "github_immutable_subject_prefix must look like repo:<owner>@<owner id>/<name>@<repo id>."
  }
}

variable "github_branch" {
  description = "The only branch whose pushes may assume fse-gha-plan-push. The fse workflows run on this branch only."
  type        = string
  default     = "main"
  validation {
    condition     = can(regex("^[A-Za-z0-9_./-]+$", var.github_branch))
    error_message = "github_branch must be a plain branch name."
  }
}
variable "github_environment" {
  description = "GitHub environment that gates every job with AWS credentials beyond the image push. IAM matches it case-sensitively; restrict its deployment branches to github_branch."
  type        = string
  default     = "fse-production"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.github_environment))
    error_message = "github_environment must be a plain environment name."
  }
}
variable "state_bucket_name" {
  description = "State bucket name. null means skylit-fse-tfstate-<account>-<region>."
  type        = string
  default     = null
}

variable "create_apply_role" {
  description = "Create fse-gha-apply (AdministratorAccess, github_environment only) so CI can run terraform apply. On by default, because the live stack has it: an apply with false destroys the role. Set false only to stop CI applies."
  type        = bool
  default     = true
}

variable "ecr_repository_names" {
  description = "ECR repositories fse-gha-plan-push may push to and pull layers from (fse, plus the console's fse-ui and fse-edge). Pushing to a repository that does not exist just fails."
  type        = list(string)
  default     = ["fse", "fse-ui", "fse-edge"]
}

variable "name_prefix" {
  description = "Prefix for role names. The main stack uses the same prefix for fse-batch-task, fse-batch-exec and fse-runner."
  type        = string
  default     = "fse"
}
