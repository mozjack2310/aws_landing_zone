terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  backend "s3" {
    bucket       = "corp-platform-tfstate-052535487590-us-east-1"
    key          = "network-hub/terraform.tfstate"
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
      Layer      = "Network-Hub"
      ManagedBy  = "Terraform"
      Project    = "ForRad-LandingZone"
      CostCenter = "CC-4011"
    }
  }
}

variable "aws_region" {
  type        = string
  description = "Primary region for Network Hub control plane"
  default     = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.aws_region)
    error_message = "Deployment restricted to us-east-1 or us-east-2 per SCP-01."
  }
}

variable "enable_transit_gateway" {
  type        = bool
  description = "Keep false in Phase 1 until Phase 2 hybrid interconnect is active"
  default     = false
}

# Corporate IPAM Supernet Map (ADR-001 / ADR-005 — Non-overlapping with On-Prem 10.10.0.0/16)
locals {
  corporate_supernet = "10.100.0.0/16"
  spoke_allocations = {
    non_prod = {
      vpc_cidr    = "10.100.16.0/20"
      app_subnets = ["10.100.20.0/22", "10.100.24.0/22"]
      db_subnets  = ["10.100.28.0/24", "10.100.29.0/24"]
      tgw_subnets = ["10.100.31.224/28", "10.100.31.240/28"]
    }
    prod = {
      vpc_cidr    = "10.100.32.0/20"
      app_subnets = ["10.100.32.0/22", "10.100.36.0/22"]
      db_subnets  = ["10.100.40.0/24", "10.100.41.0/24"]
      tgw_subnets = ["10.100.47.224/28", "10.100.47.240/28"]
    }
  }
}

resource "aws_ec2_transit_gateway" "hub" {
  count                           = var.enable_transit_gateway ? 1 : 0
  description                     = "ForRad Hybrid Cloud Transit Gateway (MTU 8500)"
  amazon_side_asn                 = 64512
  auto_accept_shared_attachments  = "disable"
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"
  dns_support                     = "enable"
  vpn_ecmp_support                = "enable"

  tags = {
    Name = "tgw-forrad-network-hub"
  }
}

output "supernet_allocation_map" {
  description = "Authoritative CIDR allocation map for spoke VPC vending"
  value       = local.spoke_allocations
}
