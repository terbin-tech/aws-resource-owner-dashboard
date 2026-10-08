provider "aws" {
  region = local.region

  default_tags {
    tags = {
      Project   = "aws-resource-owner-dashboard"
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}
