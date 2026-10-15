# --- 7. Zero-Trust GitHub Actions OIDC Federation (ADR-002 / ADR-005 — $0.00/mo Idle) ---

variable "github_repo" {
  type        = string
  description = "GitHub owner/repository allowed to assume the CI/CD OIDC role"
  default     = "mozjack2310/aws_landing_zone"
}

locals {
  github_oidc_domain = "token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = "https://${local.github_oidc_domain}"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_actions_pac_drift" {
  name        = "GitHubActionsLandingZoneRole"
  description = "Zero-trust OIDC role for GitHub Actions Shift-Left PaC and Nightly Drift Detection"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowGitHubActionsOIDC"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.github_oidc_domain}:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "${local.github_oidc_domain}:sub" = "repo:${var.github_repo}:*"
          }
        }
      }
    ]
  })
}

# Read-only permissions across the account + S3 state read/lock access for `terraform plan`
resource "aws_iam_role_policy_attachment" "github_oidc_readonly" {
  role       = aws_iam_role.github_actions_pac_drift.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "github_oidc_tfstate_lock" {
  name = "GitHubActionsTfStateLockAccess"
  role = aws_iam_role.github_actions_pac_drift.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowS3StateLockFileAndKMSDecrypt"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = [
          "arn:aws:s3:::corp-platform-tfstate-<AWS_ACCOUNT_ID>-us-east-1",
          "arn:aws:s3:::corp-platform-tfstate-<AWS_ACCOUNT_ID>-us-east-1/*",
          "arn:aws:kms:us-east-1:<AWS_ACCOUNT_ID>:key/*"
        ]
      }
    ]
  })
}

output "github_actions_oidc_role_arn" {
  value = aws_iam_role.github_actions_pac_drift.arn
}
