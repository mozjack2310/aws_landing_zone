# spoke-vending/main.tf
# Layer 4: Parameterized Spoke VPC, Gateway Endpoints, Zero-Trust Edge Role,
# Hot DynamoDB State Store, and Cold S3 Parquet Lakehouse + Glue Partition Projection
# (Provider and terraform version constraints are defined in spoke-vending/versions.tf)

data "aws_caller_identity" "current" {}

data "terraform_remote_state" "governance" {
  backend = "s3"
  config = {
    bucket = "corp-platform-tfstate-052535487590-us-east-1"
    key    = "governance/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  azs               = ["${var.aws_region}a", "${var.aws_region}b"]
  approved_stations = ["KBMX", "KHTX", "KMXX", "KMOB"]
}

# 1. Spoke Workload VPC
resource "aws_vpc" "spoke" {
  #checkov:skip=CKV2_AWS_11:Zero-Idle FinOps baseline defers CloudWatch VPC Flow Log ingestion fees in dev canary
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "forrad-${var.environment}-spoke-vpc"
  }
}

resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.spoke.id
  tags = {
    Name = "forrad-${var.environment}-default-sg-locked"
  }
}

# 2. Isolated Tiers: App, Data, and Transit Subnets across 2 AZs
resource "aws_subnet" "app" {
  count             = 2
  vpc_id            = aws_vpc.spoke.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = local.azs[count.index]

  tags = {
    Name = "forrad-${var.environment}-app-${local.azs[count.index]}"
    Tier = "Application"
  }
}

resource "aws_subnet" "data" {
  count             = 2
  vpc_id            = aws_vpc.spoke.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 2)
  availability_zone = local.azs[count.index]

  tags = {
    Name = "forrad-${var.environment}-data-${local.azs[count.index]}"
    Tier = "Data"
  }
}

resource "aws_subnet" "transit" {
  count             = 2
  vpc_id            = aws_vpc.spoke.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 6, count.index + 16)
  availability_zone = local.azs[count.index]

  tags = {
    Name = "forrad-${var.environment}-transit-${local.azs[count.index]}"
    Tier = "Transit"
  }
}

# 3. Private Route Table
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.spoke.id
  tags = {
    Name = "forrad-${var.environment}-private-rt"
  }
}

resource "aws_route_table_association" "app" {
  count          = 2
  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "data" {
  count          = 2
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.private.id
}

# 4. Zero-Cost Gateway VPC Endpoints for S3 & DynamoDB ($0.00/mo)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.spoke.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "forrad-${var.environment}-s3-gw-endpoint"
  }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.spoke.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "forrad-${var.environment}-dynamodb-gw-endpoint"
  }
}

# 5. Hot State Store: On-Demand DynamoDB Telemetry Table ($0.00/mo Idle)
resource "aws_dynamodb_table" "hybrid_telemetry" {
  #checkov:skip=CKV_AWS_119:AWS-owned key used instead of $1.00/mo KMS CMK per FinOps zero-idle baseline
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

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }

  tags = {
    Name = "forrad-hybrid-telemetry-state-${var.environment}"
    Tier = "HotState"
  }
}

# 6. Cold Lakehouse Store: S3 Bucket for Columnar Parquet Archives ($0.00/mo Idle via SSE-S3)
resource "aws_s3_bucket" "cold_lakehouse" {
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) enforced at $0.00/mo instead of $1.00/mo KMS CMK per FinOps ADR-003
  #checkov:skip=CKV_AWS_144:Single-region dev canary lakehouse avoids cross-region replication data transfer costs
  #checkov:skip=CKV_AWS_18:S3 server access logging omitted in dev canary to avoid recursive bucket storage bloat
  #checkov:skip=CKV2_AWS_62:Athena Partition Projection queries S3 directly without S3 Event Notifications
  bucket = "forrad-cold-lakehouse-${var.environment}-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "forrad-cold-lakehouse-${var.environment}"
    Tier = "ColdLakehouse"
  }
}

resource "aws_s3_bucket_versioning" "cold_lakehouse" {
  bucket = aws_s3_bucket.cold_lakehouse.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cold_lakehouse" {
  bucket = aws_s3_bucket.cold_lakehouse.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "cold_lakehouse" {
  bucket                  = aws_s3_bucket.cold_lakehouse.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "cold_lakehouse" {
  bucket = aws_s3_bucket.cold_lakehouse.id

  rule {
    id     = "expire-cold-parquet-and-abort-multiparts"
    status = "Enabled"
    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    expiration {
      days = 90
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# 7. Zero-Trust Edge Broker IAM Role & Profile (Roles Anywhere X.509 mTLS)
data "aws_iam_policy_document" "edge_broker_trust" {
  statement {
    sid    = "AllowRolesAnywhereAssumeWithPinnedCAAndCN"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["rolesanywhere.amazonaws.com"]
    }
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
      "sts:SetSourceIdentity"
    ]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [data.terraform_remote_state.governance.outputs.rolesanywhere_trust_anchor_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = ["forrad-edge-broker-01"]
    }
  }
}

resource "aws_iam_role" "edge_broker" {
  name               = "ForRadEdgeBrokerRole-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.edge_broker_trust.json
}

# Layer 5 Tenant Isolation: Pin DynamoDB LeadingKeys + Scoped S3 Cold Lakehouse Prefix Writes
data "aws_iam_policy_document" "edge_broker_least_privilege" {
  statement {
    sid    = "ScopedDynamoDBStationTenantIsolation"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:GetItem",
      "dynamodb:Query"
    ]
    resources = [aws_dynamodb_table.hybrid_telemetry.arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = local.approved_stations
    }
  }

  statement {
    sid       = "ScopedDynamoDBDescribeTableMetadata"
    effect    = "Allow"
    actions   = ["dynamodb:DescribeTable"]
    resources = [aws_dynamodb_table.hybrid_telemetry.arn]
  }

  statement {
    sid    = "ScopedColdLakehouseParquetPut"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject"
    ]
    resources = ["${aws_s3_bucket.cold_lakehouse.arn}/telemetry/*"]
  }

  statement {
    sid       = "ScopedColdLakehouseBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.cold_lakehouse.arn]
  }
}

resource "aws_iam_role_policy" "edge_broker_ddb" {
  name   = "ForRadEdgeBrokerLeastPrivilegePolicy"
  role   = aws_iam_role.edge_broker.id
  policy = data.aws_iam_policy_document.edge_broker_least_privilege.json
}

resource "aws_rolesanywhere_profile" "edge_broker" {
  name             = "forrad-edge-broker-profile-${var.environment}"
  role_arns        = [aws_iam_role.edge_broker.arn]
  enabled          = true
  duration_seconds = 3600
}

# Enforce TLS in Transit on Cold Lakehouse Bucket
data "aws_iam_policy_document" "cold_lakehouse_perimeter" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.cold_lakehouse.arn,
      "${aws_s3_bucket.cold_lakehouse.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "cold_lakehouse" {
  bucket = aws_s3_bucket.cold_lakehouse.id
  policy = data.aws_iam_policy_document.cold_lakehouse_perimeter.json
}

# 8. Serverless Analytics: AWS Glue Catalog Database & Partition-Projected Table ($0.00/mo Idle)
resource "aws_glue_catalog_database" "lakehouse" {
  name        = "forrad_telemetry_lakehouse_${var.environment}"
  description = "Zero-idle serverless Glue catalog for ForRad NEXRAD cold Parquet archives"
}

resource "aws_glue_catalog_table" "radar_sweeps_cold" {
  name          = "radar_sweeps_cold"
  database_name = aws_glue_catalog_database.lakehouse.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                       = "TRUE"
    "parquet.compression"          = "SNAPPY"
    "projection.enabled"           = "true"
    "projection.station_id.type"   = "enum"
    "projection.station_id.values" = join(",", local.approved_stations)
    "projection.day.type"          = "date"
    "projection.day.format"        = "yyyy-MM-dd"
    "projection.day.range"         = "2026-01-01,NOW"
    "projection.day.interval"      = "1"
    "projection.day.interval.unit" = "DAYS"
    "storage.location.template"    = "s3://${aws_s3_bucket.cold_lakehouse.bucket}/telemetry/station_id=$${station_id}/day=$${day}"
  }

  partition_keys {
    name = "station_id"
    type = "string"
  }

  partition_keys {
    name = "day"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.cold_lakehouse.bucket}/telemetry/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "ParquetHiveSerDe"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "frame_timestamp"
      type = "bigint"
    }
    columns {
      name = "location"
      type = "string"
    }
    columns {
      name = "radar_status"
      type = "string"
    }
    columns {
      name = "latency_seconds"
      type = "double"
    }
    columns {
      name = "queue_lag_seconds"
      type = "bigint"
    }
    columns {
      name = "idempotency_token"
      type = "string"
    }
    columns {
      name = "redis_stream_id"
      type = "string"
    }
    columns {
      name = "schema_version"
      type = "string"
    }
  }
}
