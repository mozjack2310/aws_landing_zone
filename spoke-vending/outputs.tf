# spoke-vending/outputs.tf

output "spoke_vpc_id" {
  description = "ID of the Spoke Workload VPC"
  value       = aws_vpc.spoke.id
}

output "s3_gateway_endpoint_id" {
  description = "Zero-cost S3 Gateway Endpoint ID"
  value       = aws_vpc_endpoint.s3.id
}

output "dynamodb_gateway_endpoint_id" {
  description = "Zero-cost DynamoDB Gateway Endpoint ID"
  value       = aws_vpc_endpoint.dynamodb.id
}

output "hybrid_telemetry_table_name" {
  description = "Hot State DynamoDB Table Name"
  value       = aws_dynamodb_table.hybrid_telemetry.name
}

output "cold_lakehouse_bucket_name" {
  description = "Cold Lakehouse S3 Bucket Name for Parquet Compaction"
  value       = aws_s3_bucket.cold_lakehouse.bucket
}

output "glue_database_name" {
  description = "Serverless Glue Catalog Database Name"
  value       = aws_glue_catalog_database.lakehouse.name
}

output "glue_table_name" {
  description = "Partition-Projected Glue Table Name for Athena"
  value       = aws_glue_catalog_table.radar_sweeps_cold.name
}

output "edge_broker_role_arn" {
  description = "IAM Role ARN assumed by on-prem edge broker via X.509 mTLS"
  value       = aws_iam_role.edge_broker.arn
}

output "edge_broker_profile_arn" {
  description = "IAM Roles Anywhere Profile ARN"
  value       = aws_rolesanywhere_profile.edge_broker.arn
}
