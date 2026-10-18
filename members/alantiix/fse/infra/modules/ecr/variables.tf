variable "name" {
  description = "Repository name (fse, fse-ui or fse-edge). The bootstrap stack's push role covers bootstrap var.ecr_repository_names."
  type        = string
  default     = "fse"
}

variable "keep_images" {
  description = "Images kept by the lifecycle policy (any tag status)."
  type        = number
  default     = 10
}
