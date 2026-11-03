terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

# ADR-004: Explicit TPS rate-limit mitigation against Control Tower daily sweeps
provider "aws" {
  region      = var.aws_region
  max_retries = 9
  retry_mode  = "adaptive"
}
