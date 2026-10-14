data "aws_availability_zones" "available" {
  state = "available"
}

# Pull the Roles Anywhere Trust Anchor ARN directly from Layer 2 (governance) state
data "terraform_remote_state" "governance" {
  backend = "s3"
  config = {
    bucket = "corp-platform-tfstate-<AWS_ACCOUNT_ID>-us-east-1"
    key    = "governance/terraform.tfstate"
    region = "us-east-1"
  }
}

# --- 1. Core Spoke VPC (10.100.16.0/20) ---
resource "aws_vpc" "spoke" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "spoke-${var.environment}-vpc"
  }
}

# Default SG Hardening (ADR-005 Shift-Left PaC Compliance: Zero Ingress/Egress)
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.spoke.id

  tags = {
    Name = "default-restricted-sg"
  }
}

# --- 2. Multi-AZ Subnet Tiering (Matches ADR-001 Exact Boundaries) ---
# Application Private Subnets (/22): 10.100.20.0/22 (AZ-a), 10.100.24.0/22 (AZ-b)
resource "aws_subnet" "application" {
  count             = 2
  vpc_id            = aws_vpc.spoke.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 2, count.index + 1)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "subnet-${var.environment}-app-${data.aws_availability_zones.available.names[count.index]}"
    Tier = "Application"
  }
}

# Database Isolated Subnets (/24): 10.100.28.0/24 (AZ-a), 10.100.29.0/24 (AZ-b)
resource "aws_subnet" "database" {
  count             = 2
  vpc_id            = aws_vpc.spoke.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 12)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "subnet-${var.environment}-db-${data.aws_availability_zones.available.names[count.index]}"
    Tier = "Database"
  }
}

# Pre-Staged TGW Transit Subnets (/28): 10.100.31.224/28 (AZ-a), 10.100.31.240/28 (AZ-b)
resource "aws_subnet" "tgw" {
  count             = 2
  vpc_id            = aws_vpc.spoke.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 254)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "subnet-${var.environment}-tgw-${data.aws_availability_zones.available.names[count.index]}"
    Tier = "Transit"
  }
}

# --- 3. Routing & Zero-Cost Gateway Endpoints ($0.00/mo Idle) ---
resource "aws_route_table" "application" {
  vpc_id = aws_vpc.spoke.id

  tags = {
    Name = "rt-${var.environment}-application"
  }
}

resource "aws_route_table_association" "application" {
  count          = 2
  subnet_id      = aws_subnet.application[count.index].id
  route_table_id = aws_route_table.application.id
}

resource "aws_vpc_endpoint" "s3_gateway" {
  vpc_id            = aws_vpc.spoke.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.application.id]

  tags = {
    Name = "vpce-${var.environment}-s3-gateway"
  }
}

resource "aws_vpc_endpoint" "dynamodb_gateway" {
  vpc_id            = aws_vpc.spoke.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.application.id]

  tags = {
    Name = "vpce-${var.environment}-dynamodb-gateway"
  }
}

# --- 4. FinOps-Gated Transit Gateway Attachment & Flow Logs ---
resource "aws_ec2_transit_gateway_vpc_attachment" "spoke" {
  count              = var.enable_tgw_attachment ? 1 : 0
  transit_gateway_id = var.transit_gateway_id
  vpc_id             = aws_vpc.spoke.id
  subnet_ids         = aws_subnet.tgw[*].id

  dns_support                                     = "enable"
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false

  tags = {
    Name = "tgw-attachment-${var.environment}"
  }
}

resource "aws_flow_log" "spoke" {
  count                = var.enable_flow_logs ? 1 : 0
  log_destination_type = "s3"
  log_destination      = var.log_archive_s3_bucket_arn
  traffic_type         = "ALL"
  vpc_id               = aws_vpc.spoke.id

  destination_options {
    file_format        = "parquet"
    per_hour_partition  = true
  }

  tags = {
    Name = "flowlogs-${var.environment}"
  }
}

# --- 5. Serverless HDEF State & Telemetry Table ($0.00/mo Idle) ---
resource "aws_dynamodb_table" "forrad_telemetry_state" {
  name         = "forrad-hybrid-telemetry-state-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "StationID"
  range_key    = "FrameTimestamp"

  attribute {
    name = "StationID"
    type = "S"
  }

  attribute {
    name = "FrameTimestamp"
    type = "N"
  }

  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }

  server_side_encryption {
    enabled = true # Uses AWS-owned KMS key ($0.00/mo fee) to preserve $1.00 budget tripwire
  }

  tags = {
    Name = "forrad-hybrid-telemetry-state-${var.environment}"
  }
}

# --- 6. Zero-Trust Edge Broker Identity: IAM Role & Roles Anywhere Profile (ADR-002) ---
resource "aws_iam_role" "forrad_edge_broker" {
  name        = "ForRadEdgeBrokerRole-${var.environment}"
  description = "Assumed via X.509 mTLS by on-premises ForRad edge brokers (Zero Static Keys)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowRolesAnywhereX509mTLS"
        Effect = "Allow"
        Principal = {
          Service = "rolesanywhere.amazonaws.com"
        }
        Action = [
          "sts:AssumeRole",
          "sts:TagSession",
          "sts:SetSourceIdentity"
        ]
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = data.terraform_remote_state.governance.outputs.rolesanywhere_trust_anchor_arn
          }
          StringEquals = {
            "aws:PrincipalTag/x509Subject/CN" = "forrad-edge-broker-01"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "forrad_edge_dynamodb_sync" {
  name = "ForRadEdgeDynamoDBSyncPolicy"
  role = aws_iam_role.forrad_edge_broker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ScopedTelemetryTableAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:BatchWriteItem",
          "dynamodb:DescribeTable"
        ]
        Resource = aws_dynamodb_table.forrad_telemetry_state.arn
      }
    ]
  })
}

resource "aws_rolesanywhere_profile" "forrad_edge_profile" {
  name             = "forrad-edge-broker-profile-${var.environment}"
  enabled          = true
  role_arns        = [aws_iam_role.forrad_edge_broker.arn]
  session_policy   = null
  duration_seconds = 3600 # 1-Hour STS Token Ceiling per ADR-002
}

output "spoke_vpc_id" {
  value = aws_vpc.spoke.id
}

output "spoke_subnet_cidrs" {
  value = {
    application = aws_subnet.application[*].cidr_block
    database    = aws_subnet.database[*].cidr_block
    transit_tgw = aws_subnet.tgw[*].cidr_block
  }
}

output "telemetry_dynamodb_table" {
  value = aws_dynamodb_table.forrad_telemetry_state.name
}

output "edge_broker_identity" {
  value = {
    trust_anchor_arn = data.terraform_remote_state.governance.outputs.rolesanywhere_trust_anchor_arn
    profile_arn      = aws_rolesanywhere_profile.forrad_edge_profile.arn
    role_arn         = aws_iam_role.forrad_edge_broker.arn
  }
}
