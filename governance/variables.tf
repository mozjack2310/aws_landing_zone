variable "aws_region" {
  type        = string
  description = "Primary AWS region for governance control plane"
  default     = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.aws_region)
    error_message = "Deployment restricted to us-east-1 or us-east-2 per enterprise SCP."
  }
}

variable "budget_alert_email" {
  type        = string
  description = "Email address for the 80% ($4.00) FinOps budget circuit breaker alert"
  default     = "secops@forradwx.app"
}

variable "trust_anchor_x509_cert_path" {
  type        = string
  description = "Path to the offline Root CA PEM file for IAM Roles Anywhere"
  default     = "../certs/forrad-root-ca.pem"
}
