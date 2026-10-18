provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "fse"
      ManagedBy = "terraform"
    }
  }
}

# CloudFront only accepts ACM certificates from us-east-1 (modules/ui_edge).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "fse"
      ManagedBy = "terraform"
    }
  }
}
