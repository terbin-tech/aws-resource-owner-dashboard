# No SNS encryption: CloudWatch alarms can't publish to a topic that uses the AWS managed
# aws/sns key, and a customer managed key costs more than the whole stack.
resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

# Off by default, so no alarm email leaves the account. Set alarm_email to subscribe one address.
resource "aws_sns_topic_subscription" "alarm_email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.name}-errors"
  alarm_description   = "A scheduled run failed. Check the log group (30 days), fix, run the smoke test."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.dashboard.function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# 26 hours in a row without a run, like the page's stale banner. Hourly periods, because
# a 1-day period follows UTC days, not the 07:00 and 17:00 Berlin runs.
# CloudWatch may also count an older real datapoint just outside the 26 h, so it can fire a
# few hours later than 26 h. Right after creation it is in ALARM: no datapoints yet.
resource "aws_cloudwatch_metric_alarm" "no_invocations" {
  alarm_name          = "${local.name}-no-run"
  alarm_description   = "No run for about 26 hours or a little more: the schedule may be broken or disabled by hand."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.dashboard.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 26
  datapoints_to_alarm = 26
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  actions_enabled     = var.schedule_enabled # the kill switch doesn't page anyone
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_log_metric_filter" "incomplete" {
  name           = "${local.name}-incomplete"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.incomplete_count > 0 }"

  metric_transformation {
    name      = "IncompleteCount"
    namespace = "ResourceOwnerDashboard"
    value     = "$.incomplete_count"
  }
}

resource "aws_cloudwatch_metric_alarm" "incomplete" {
  alarm_name          = "${local.name}-incomplete"
  alarm_description   = "Part of a scheduled run was incomplete. Open the page: its banners name what."
  namespace           = "ResourceOwnerDashboard"
  metric_name         = "IncompleteCount"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
