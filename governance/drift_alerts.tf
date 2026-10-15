# --- 6. Event-Driven Control Plane Drift & Tamper Alerting (ADR-003 / ADR-005 — $0.00/mo Idle) ---

resource "aws_sns_topic" "security_governance_alerts" {
  name = "forrad-security-governance-alerts"
}

resource "aws_sns_topic_subscription" "security_alert_email" {
  topic_arn = aws_sns_topic.security_governance_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_alert_email
}

# Intercepts SCP tampering, Roles Anywhere CA disabling, static IAM key creation, and Control Tower drift
resource "aws_cloudwatch_event_rule" "control_plane_drift" {
  name        = "forrad-control-plane-security-drift"
  description = "Alerts on SCP detachment, Roles Anywhere Trust Anchor tampering, static IAM key creation, or Control Tower drift"

  event_pattern = jsonencode({
    detail-type = [
      "AWS API Call via CloudTrail",
      "AWS Service Event via CloudTrail"
    ]
    detail = {
      eventSource = [
        "organizations.amazonaws.com",
        "rolesanywhere.amazonaws.com",
        "iam.amazonaws.com",
        "controltower.amazonaws.com"
      ]
      eventName = [
        "DetachPolicy",
        "DeletePolicy",
        "UpdatePolicy",
        "DisablePolicyType",
        "DeleteTrustAnchor",
        "DisableTrustAnchor",
        "DeleteProfile",
        "DisableProfile",
        "CreateUser",
        "CreateAccessKey"
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "sns_drift_target" {
  rule      = aws_cloudwatch_event_rule.control_plane_drift.name
  target_id = "SendSecurityDriftToSNS"
  arn       = aws_sns_topic.security_governance_alerts.arn
}

resource "aws_sns_topic_policy" "allow_eventbridge_publish" {
  arn = aws_sns_topic.security_governance_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEventBridgePublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.security_governance_alerts.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_rule.control_plane_drift.arn
          }
        }
      }
    ]
  })
}

output "security_alerts_sns_topic_arn" {
  value = aws_sns_topic.security_governance_alerts.arn
}
