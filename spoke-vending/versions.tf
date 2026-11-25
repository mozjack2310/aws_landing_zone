terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region      = var.aws_region
  max_retries = 9
  retry_mode  = "adaptive"

  dynamic "assume_role" {
    for_each = var.target_account_role_arn != "" ? [var.target_account_role_arn] : []
    content {
      role_arn     = assume_role.value
      session_name = "TerraformSpokeVending-${var.environment}"
    }
  }

  default_tags {
    tags = {
      Environment = var.environment
      ManagedBy   = "Terraform"
      Project     = "ForRad-Hybrid-Fabric"
      CostCenter  = "CC-4011"
    }
  }
}
