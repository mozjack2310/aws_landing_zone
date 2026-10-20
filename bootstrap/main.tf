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
}

variable "aws_region" {
  type        = string
  description = "Primary AWS Landing Zone home region"
  default     = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.aws_region)
    error_message = "Deployment restricted to us-east-1 or us-east-2 per enterprise SCP."
  }
}

data "aws_caller_identity" "current" {}
data "aws_organizations_organization" "org" {}

# 1. Remote State S3 Bucket (corp-platform-tfstate-<ACCOUNT_ID>-us-east-1)
resource "aws_s3_bucket" "tfstate" {
  bucket        = "corp-platform-tfstate-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate_versioning" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate_encryption" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate_public_block" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "tfstate_enforce_tls" {
  bucket = aws_s3_bucket.tfstate.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforceTLSRequestsOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.tfstate.arn,
          "${aws_s3_bucket.tfstate.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.tfstate_public_block]
}

# 2. DynamoDB State Lock Table ($0.00 Idle Run-Cost via Default AWS Owned AES-256 Key)
resource "aws_dynamodb_table" "tflocks" {
  name         = "corp-platform-tflocks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  #checkov:skip=CKV_AWS_119:FinOps ADR - Using $0.00 AWS Owned AES-256 Encryption
  #checkov:skip=CKV_AWS_28:FinOps ADR - Lock table contains ephemeral LockIDs only
  server_side_encryption {
    enabled = false
  }
}

output "tfstate_bucket_name" {
  value = aws_s3_bucket.tfstate.id
}

output "tflocks_table_name" {
  value = aws_dynamodb_table.tflocks.name
}
