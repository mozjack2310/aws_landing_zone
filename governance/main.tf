data "aws_organizations_organization" "org" {}
data "aws_ssoadmin_instances" "main" {}

locals {
  root_id      = data.aws_organizations_organization.org.roots[0].id
  instance_arn = tolist(data.aws_ssoadmin_instances.main.arns)[0]
}

# --- 1. FinOps Circuit Breaker ($5.00/mo Cap, Alert at $4.00) ---
resource "aws_budgets_budget" "platform_cost_cap" {
  name         = "forrad-landing-zone-monthly-cap"
  budget_type  = "COST"
  limit_amount = "5.0"
  limit_unit   = "USD"
  time_unit   = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}

# --- 2. Foundational Organizational Units (ADR-001) ---
resource "aws_organizations_organizational_unit" "security" {
  name      = "Security"
  parent_id = local.root_id

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "aws_organizations_organizational_unit" "infrastructure" {
  name      = "Infrastructure"
  parent_id = local.root_id

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "aws_organizations_organizational_unit" "workloads" {
  name      = "Workloads"
  parent_id = local.root_id

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "aws_organizations_organizational_unit" "sandbox" {
  name      = "Sandbox"
  parent_id = local.root_id

  lifecycle {
    ignore_changes = [tags]
  }
}

# --- 3. Preventive Service Control Policies (ADR-005) ---
resource "aws_organizations_policy" "scp_region_boundary" {
  name        = "SCP-01-Enforce-Region-Boundary"
  description = "Restricts provisioning strictly to us-east-1 and us-east-2, exempting global services and Control Tower."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnforceRegionBoundary"
        Effect = "Deny"
        NotAction = [
          "a4b:*", "acm:*", "aws-marketplace-management:*", "aws-marketplace:*",
          "aws-portal:*", "budgets:*", "ce:*", "chime:*", "cloudfront:*",
          "config:*", "cur:*", "directconnect:*", "ec2:DescribeRegions",
          "fms:*", "globalaccelerator:*", "health:*", "iam:*", "identitystore:*",
          "importexport:*", "kms:*", "mobileanalytics:*", "networkmanager:*",
          "organizations:*", "pricing:*", "rolesanywhere:*", "route53:*",
          "route53domains:*", "s3:GetAccountPublicAccessBlock",
          "s3:ListAllMyBuckets", "s3:PutAccountPublicAccessBlock", "shield:*",
          "signin:*", "sso:*", "sts:*", "support:*", "trustedadvisor:*",
          "waf-regional:*", "waf:*", "wafv2:*", "wellarchitected:*"
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "aws:RequestedRegion" = ["us-east-1", "us-east-2"]
          }
          ArnNotLike = {
            "aws:PrincipalARN" = ["arn:aws:iam::*:role/AWSControlTowerExecution"]
          }
        }
      }
    ]
  })
}

resource "aws_organizations_policy" "scp_security_baseline" {
  name        = "SCP-02-Protect-Security-Baselines"
  description = "Prevents disabling CloudTrail, GuardDuty, Config, or executing member root API calls."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyModifyingSecurityBaselines"
        Effect = "Deny"
        Action = [
          "cloudtrail:DeleteTrail", "cloudtrail:StopLogging", "cloudtrail:UpdateTrail",
          "config:DeleteConfigRule", "config:DeleteConfigurationRecorder",
          "config:DeleteDeliveryChannel", "config:StopConfigurationRecorder",
          "guardduty:DeleteDetector", "guardduty:DisassociateFromMasterAccount",
          "guardduty:StopMonitoringMembers", "securityhub:DisableSecurityHub",
          "securityhub:DisassociateFromMasterAccount"
        ]
        Resource = "*"
        Condition = {
          ArnNotLike = {
            "aws:PrincipalARN" = [
              "arn:aws:iam::*:role/AWSControlTowerExecution",
              "arn:aws:iam::*:role/AWSControlTowerAdmin"
            ]
          }
        }
      },
      {
        Sid      = "PreventLeavingOrganization"
        Effect   = "Deny"
        Action   = ["organizations:LeaveOrganization"]
        Resource = "*"
      },
      {
        Sid      = "DenyRootUserExecution"
        Effect   = "Deny"
        Action   = "*"
        Resource = "*"
        Condition = {
          StringLike = {
            "aws:PrincipalArn" = "arn:aws:iam::*:root"
          }
        }
      }
    ]
  })
}

resource "aws_organizations_policy" "scp_workload_invariants" {
  name        = "SCP-03-Workload-Perimeter-Invariants"
  description = "Blocks local IAM users/static keys and prevents disabling S3 Public Access Blocks."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyLocalIAMUsers"
        Effect   = "Deny"
        Action   = ["iam:CreateUser", "iam:CreateAccessKey"]
        Resource = "*"
      },
      {
        Sid      = "DenyDisablingS3PublicBlock"
        Effect   = "Deny"
        Action   = ["s3:DeleteAccountPublicAccessBlock", "s3:PutAccountPublicAccessBlock"]
        Resource = "*"
        Condition = {
          ArnNotLike = {
            "aws:PrincipalARN" = ["arn:aws:iam::*:role/AWSControlTowerExecution"]
          }
        }
      }
    ]
  })
}

resource "aws_organizations_policy_attachment" "root_region_boundary" {
  policy_id = aws_organizations_policy.scp_region_boundary.id
  target_id = local.root_id
}

resource "aws_organizations_policy_attachment" "root_security_baseline" {
  policy_id  = aws_organizations_policy.scp_security_baseline.id
  target_id  = local.root_id
  depends_on = [aws_organizations_policy_attachment.root_region_boundary]
}

resource "aws_organizations_policy_attachment" "workloads_invariants" {
  policy_id  = aws_organizations_policy.scp_workload_invariants.id
  target_id  = aws_organizations_organizational_unit.workloads.id
  depends_on = [aws_organizations_policy_attachment.root_security_baseline]
}

# --- 4. IAM Identity Center Baseline Permission Sets (ADR-002) ---
resource "aws_ssoadmin_permission_set" "break_glass" {
  name             = "AWSAdminAccess-BreakGlass"
  description      = "Emergency break-glass access. 1-hour session limit."
  instance_arn     = local.instance_arn
  session_duration = "PT1H"
}

resource "aws_ssoadmin_managed_policy_attachment" "break_glass_admin" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.break_glass.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_ssoadmin_permission_set" "network_admin" {
  name             = "AWSNetworkAdministrator"
  description      = "Scoped access to VPC, Transit Gateway, Route 53, and Direct Connect."
  instance_arn     = local.instance_arn
  session_duration = "PT4H"
}

resource "aws_ssoadmin_permission_set_inline_policy" "network_admin_policy" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.network_admin.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowNetworkServices"
        Effect = "Allow"
        Action = [
          "ec2:*Vpc*", "ec2:*Subnet*", "ec2:*RouteTable*",
          "ec2:*TransitGateway*", "ec2:*SecurityGroup*", "ec2:*Ipam*",
          "route53:*", "route53resolver:*", "directconnect:*",
          "networkmanager:*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_ssoadmin_permission_set" "security_auditor" {
  name             = "AWSSecurityAuditor"
  description      = "Delegated read-only security auditing across all organization member accounts."
  instance_arn     = local.instance_arn
  session_duration = "PT8H"
}

resource "aws_ssoadmin_managed_policy_attachment" "audit_security" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.security_auditor.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/SecurityAudit"
}

resource "aws_ssoadmin_managed_policy_attachment" "audit_view_only" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.security_auditor.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"
}

resource "aws_ssoadmin_permission_set" "platform_engineer" {
  name             = "AWSPlatformEngineer"
  description      = "Platform engineering role; cannot modify IAM permissions or alter SCP boundaries."
  instance_arn     = local.instance_arn
  session_duration = "PT4H"
}

resource "aws_ssoadmin_permission_set_inline_policy" "platform_engineer_policy" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.platform_engineer.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowWorkloadOperations"
        Effect    = "Allow"
        NotAction = ["iam:*", "organizations:*", "controltower:*"]
        Resource  = "*"
      },
      {
        Sid      = "AllowPassRoleToPipelines"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::*:role/WorkloadExecutionRole*"
      }
    ]
  })
}

# --- 5. Zero-Trust Machine Identity: X.509 Trust Anchor (ADR-002) ---
resource "aws_rolesanywhere_trust_anchor" "forrad_edge_ca" {
  name    = "forrad-hybrid-edge-ca-anchor"
  enabled = true

  source {
    source_type = "CERTIFICATE_BUNDLE"
    source_data {
      x509_certificate_data = file("${path.module}/${var.trust_anchor_x509_cert_path}")
    }
  }
}

output "ou_ids" {
  value = {
    security       = aws_organizations_organizational_unit.security.id
    infrastructure = aws_organizations_organizational_unit.infrastructure.id
    workloads      = aws_organizations_organizational_unit.workloads.id
    sandbox        = aws_organizations_organizational_unit.sandbox.id
  }
}

output "rolesanywhere_trust_anchor_arn" {
  value = aws_rolesanywhere_trust_anchor.forrad_edge_ca.arn
}
