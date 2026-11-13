# ==============================================================================
# Stage 4: Multi-Account Vending Factory (SEC01-BP01, SEC01-BP02, REL01-BP03)
# - Zero-idle cost ($0.00/mo baseline per vended AWS Organizations member account)
# - Inherits OU-level preventive SCPs (scp_region_boundary, scp_security_baseline,
#   and scp_workload_invariants defined in governance/main.tf)
# - Enforces close_on_deletion = true and pre-provisions OrganizationAccountAccessRole
# ==============================================================================

variable "vended_accounts" {
  description = "Map of member accounts to vend via AWS Organizations. Defaults to empty map ({}) for zero-drift CI/CD safety until populated via TF_VAR_vended_accounts."
  type = map(object({
    name                       = string
    email                      = string
    ou_key                     = string
    environment                = string
    role_name                  = optional(string, "OrganizationAccountAccessRole")
    iam_user_access_to_billing = optional(string, "ALLOW")
  }))
  default = {}

  validation {
    condition = alltrue([
      for k, v in var.vended_accounts : contains(["security", "infrastructure", "workloads", "sandbox"], v.ou_key)
    ])
    error_message = "Each vended account ou_key must be one of: security, infrastructure, workloads, or sandbox."
  }

  validation {
    condition = alltrue([
      for k, v in var.vended_accounts : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", v.email)) && length(v.email) >= 6 && length(v.email) <= 64
    ])
    error_message = "Each vended account email must be a valid email address between 6 and 64 characters (RFC 5233 + sub-addressing supported)."
  }

  validation {
    condition = alltrue([
      for k, v in var.vended_accounts : contains(["ALLOW", "DENY"], v.iam_user_access_to_billing)
    ])
    error_message = "iam_user_access_to_billing must be either ALLOW or DENY."
  }
}

locals {
  ou_lookup = {
    security       = aws_organizations_organizational_unit.security.id
    infrastructure = aws_organizations_organizational_unit.infrastructure.id
    workloads      = aws_organizations_organizational_unit.workloads.id
    sandbox        = aws_organizations_organizational_unit.sandbox.id
  }
}

resource "aws_organizations_account" "vended" {
  for_each = var.vended_accounts

  name                       = each.value.name
  email                      = each.value.email
  parent_id                  = local.ou_lookup[each.value.ou_key]
  role_name                  = each.value.role_name
  iam_user_access_to_billing = each.value.iam_user_access_to_billing
  close_on_deletion          = true
  create_govcloud            = false

  lifecycle {
    ignore_changes = [role_name]
  }

  tags = {
    Name        = each.value.name
    Environment = each.value.environment
    OU          = each.value.ou_key
    ManagedBy   = "Terraform"
    Layer       = "governance-account-vending"
  }
}

output "vended_account_ids" {
  description = "Map of logical account keys to their 12-digit AWS Account IDs"
  value       = { for k, acct in aws_organizations_account.vended : k => acct.id }
}

output "vended_account_bootstrap_role_arns" {
  description = "Map of logical account keys to their pre-provisioned OrganizationAccountAccessRole ARNs (SEC10-BP05)"
  value = {
    for k, acct in aws_organizations_account.vended :
    k => "arn:aws:iam::${acct.id}:role/${var.vended_accounts[k].role_name}"
  }
}
