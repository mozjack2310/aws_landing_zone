terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  backend "s3" {
    key          = "spoke-vending/forrad-dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region      = var.aws_region
  max_retries = 9
  retry_mode  = "adaptive"

  default_tags {
    tags = {
      Environment = var.environment
      ManagedBy   = "Terraform"
      Project     = "ForRad-Hybrid-Fabric"
      CostCenter  = "CC-4011"
    }
  }
}
