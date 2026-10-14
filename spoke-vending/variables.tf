variable "aws_region" {
  type        = string
  description = "Target AWS deployment region"
  default     = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.aws_region)
    error_message = "Deployment restricted to us-east-1 or us-east-2 per SCP-01."
  }
}

variable "environment" {
  type        = string
  description = "Workload environment tier"
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod", "sandbox"], var.environment)
    error_message = "Environment must be dev, staging, prod, or sandbox."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "Spoke VPC CIDR block from corporate supernet 10.100.0.0/16"
  default     = "10.100.16.0/20"
}

variable "enable_tgw_attachment" {
  type        = bool
  description = "Controls active TGW attachment. Default false to prevent $36/mo idle charge in Phase 1."
  default     = false
}

variable "transit_gateway_id" {
  type        = string
  description = "Transit Gateway ID when enable_tgw_attachment is true"
  default     = ""
}

variable "enable_flow_logs" {
  type        = bool
  description = "Controls VPC Flow Logs to S3. Default false until Log Archive bucket is active."
  default     = false
}

variable "log_archive_s3_bucket_arn" {
  type        = string
  description = "Centralized S3 Log Archive bucket ARN (ADR-003)"
  default     = "arn:aws:s3:::corp-platform-central-logs-logarchive"
}
