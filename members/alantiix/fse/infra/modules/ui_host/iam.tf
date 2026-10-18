# Console permissions (design 11.5). They sit on the runner instance role, so
# fse-edge holds them too (accepted risk, decision c). Reads under /fse/* come
# from Phase 1's ReadFseParameters. Every ARN is built from known values so
# the policies are known at plan time (tests/ui.tftest.hcl decodes them).
#
# Plugin-orders switch (/fse/control/plugin_orders): the switch bounds the
# console application and its users: no console code path can enable plugin
# orders, Combine or `fse live`. It does not bound a principal holding the
# instance-role credentials. That principal can read the ProjectX key and
# account ids (ReadFseParameters) and can launch a batch task with command
# and env overrides (EcsRunBatch). This is pre-existing and accepted
# (decision c). The IAM narrowing only removes the `/secrets/*` write path.
# Cannot: the batch exec role (`GetParameters` on `/secrets/*`) or the batch
# task role (S3 and Glue only).

locals {
  arn_prefix  = "arn:aws:%s:${var.region}:${var.account_id}"
  ecs_arn     = format(local.arn_prefix, "ecs")
  ssm_arn     = format(local.arn_prefix, "ssm")
  logs_arn    = format(local.arn_prefix, "logs")
  bucket      = var.data_bucket_arn
  ui_logs_arn = "${local.logs_arn}:log-group:${local.ui_log_group_name}"
  glue_arn    = format(local.arn_prefix, "glue")
  ecs_cluster = { ArnEquals = { "ecs:cluster" = var.cluster_arn } }
}

resource "aws_iam_role_policy" "runner_console" {
  name = "fse-console"
  role = var.runner_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EcsRunBatch"
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = ["${local.ecs_arn}:task-definition/${var.task_family}:*"]
        Condition = local.ecs_cluster
      },
      {
        Sid       = "EcsBatchTasks"
        Effect    = "Allow"
        Action    = ["ecs:DescribeTasks", "ecs:StopTask"]
        Resource  = ["${local.ecs_arn}:task/${var.cluster_name}/*"]
        Condition = local.ecs_cluster
      },
      {
        # The Service Authorization Reference lists container-instance as the
        # resource type, but a Fargate ListTasks names no container instance
        # and is evaluated against "*"; the cluster condition scopes it.
        Sid       = "EcsFindTasks"
        Effect    = "Allow"
        Action    = ["ecs:ListTasks"]
        Resource  = ["*"]
        Condition = local.ecs_cluster
      },
      {
        Sid      = "PassBatchRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [var.batch_task_role_arn, var.batch_exec_role_arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        # No prefix condition: GetObject on a missing key must return
        # NoSuchKey (S3Lock.holder), not 403. Key names are not secret.
        Sid      = "ListDataBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.bucket]
      },
      {
        Sid      = "ReadRuns"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.bucket}/runs/*"]
      },
      {
        # fse-sync-cache.sh copies the bars part of the Data_Cache to the runner's
        # disk. Read-only and named: never heatmaps/ or plugin_books/ (20 GB).
        Sid    = "ReadRunnerCache"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${local.bucket}/cache/catalog.sqlite",
          "${local.bucket}/cache/bars/*",
          "${local.bucket}/cache/equity_bars/*",
          "${local.bucket}/cache/derived/*",
          "${local.bucket}/cache/darkpool/*",
          "${local.bucket}/cache/vix/*",
        ]
      },
      {
        # A card backtest uploads a cache file S3 does not have yet. Put only,
        # on the same named prefixes ReadRunnerCache may read. Never cache/*,
        # never heatmaps/ or plugin_books/.
        Sid    = "SeedMissingCache"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = [
          "${local.bucket}/cache/catalog.sqlite",
          "${local.bucket}/cache/bars/*",
          "${local.bucket}/cache/equity_bars/*",
          "${local.bucket}/cache/derived/*",
          "${local.bucket}/cache/darkpool/*",
          "${local.bucket}/cache/vix/*",
        ]
      },
      {
        # fse-offload-recordings.sh uploads closed days of the data-plane
        # recordings; the console's fse backtest-plugin reads them back.
        # Listing uses ListDataBucket. No DeleteObject: S3 keeps the history.
        Sid      = "OffloadRecordings"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"]
        Resource = ["${local.bucket}/recordings/*"]
      },
      {
        # Config mirror and upload staging; the console never reads them back.
        Sid      = "WriteConsoleInputs"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${local.bucket}/configs/*", "${local.bucket}/uploads/*"]
      },
      {
        Sid      = "Locks"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${local.bucket}/locks/*"]
      },
      {
        Sid    = "ReadLogs"
        Effect = "Allow"
        Action = ["logs:GetLogEvents"]
        Resource = [
          "${local.logs_arn}:log-group:${var.batch_log_group}:log-stream:*",
          "${local.logs_arn}:log-group:${var.runner_log_group}:log-stream:*",
        ]
      },
      {
        # dockerd's awslogs driver (same set as Phase 1 RunnerLogs).
        Sid      = "UiLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = [local.ui_logs_arn, "${local.ui_logs_arn}:*"]
      },
      {
        Sid      = "RunnerControlDoc"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = ["${local.ssm_arn}:document/${local.control_document_name}"]
      },
      {
        Sid      = "RunnerControlInstance"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = ["${format(local.arn_prefix, "ec2")}:instance/*"]
        Condition = {
          StringEquals = { "ssm:resourceTag/Role" = var.runner_role_name }
        }
      },
      {
        # No resource type.
        Sid      = "CommandResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = ["*"]
      },
      {
        Sid    = "WriteParameters"
        Effect = "Allow"
        Action = ["ssm:PutParameter"]
        # Exact secret ARNs, never /secrets/*: a console-created
        # /fse/secrets/FSE_RUN_PLUGIN_ORDERS (or any new name) is refused, and
        # /control/* (the plugin-orders switch) is never writable.
        Resource = concat(
          [for n in var.secret_names : "${local.ssm_arn}:parameter${var.ssm_prefix}/secrets/${n}"],
          [
            "${local.ssm_arn}:parameter${aws_ssm_parameter.runner_mode.name}",
            "${local.ssm_arn}:parameter${aws_ssm_parameter.runner_config.name}",
          ],
        )
      },
      {
        Sid      = "Alarms"
        Effect   = "Allow"
        Action   = ["cloudwatch:DescribeAlarms"]
        Resource = ["${format(local.arn_prefix, "cloudwatch")}:alarm:${var.runner_role_name}-*"]
      },
      {
        # fse-ui-fetch-env.sh reads the app client secret at unit start.
        Sid      = "CognitoClient"
        Effect   = "Allow"
        Action   = ["cognito-idp:DescribeUserPoolClient"]
        Resource = [var.cognito_user_pool_arn]
      },
      {
        # Caddy's DNS-01 challenge: only the TXT record of the origin name.
        Sid      = "AcmeRecord"
        Effect   = "Allow"
        Action   = ["route53:ChangeResourceRecordSets"]
        Resource = ["arn:aws:route53:::hostedzone/${var.zone_id}"]
        Condition = {
          "ForAllValues:StringEquals" = {
            "route53:ChangeResourceRecordSetsNormalizedRecordNames" = ["_acme-challenge.${lower(trimsuffix(var.origin_domain, "."))}"]
            "route53:ChangeResourceRecordSetsRecordTypes"           = ["TXT"]
          }
          Null = { "route53:ChangeResourceRecordSetsNormalizedRecordNames" = "false" }
        }
      },
      {
        Sid      = "AcmeRead"
        Effect   = "Allow"
        Action   = ["route53:ListResourceRecordSets"]
        Resource = ["arn:aws:route53:::hostedzone/${var.zone_id}"]
      },
      {
        Sid      = "AcmeChange"
        Effect   = "Allow"
        Action   = ["route53:GetChange"]
        Resource = ["arn:aws:route53:::change/*"]
      },
      {
        Sid      = "EcrPullUi"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = [var.ui_repository_arn, var.edge_repository_arn]
      },
    ]
  })
}

# What console batch commands need inside the Fargate task beyond Phase 1's
# cache/runs/locks grant: config and upload inputs, the holdout log, the
# research sync and fse export-aws.
resource "aws_iam_role_policy" "batch_console" {
  name = "fse-batch-console"
  role = var.batch_task_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # HeadBucket (export-aws) and a 404 rather than 403 on a missing
        # state/holdout_log.jsonl need ListBucket without a prefix condition.
        Sid      = "ListDataBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.bucket]
      },
      {
        Sid      = "ReadConsoleInputs"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.bucket}/configs/*", "${local.bucket}/uploads/*"]
      },
      {
        # fse backtest-plugin --recordings-s3. Get only. Listing uses ListDataBucket.
        # No DeleteObject: the hourly offload is the writer, and it never deletes.
        Sid      = "ReadRecordings"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.bucket}/recordings/*"]
      },
      {
        Sid      = "HoldoutState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${local.bucket}/state/*"]
      },
      {
        Sid      = "ResearchAndExport"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = ["${local.bucket}/research/*", "${local.bucket}/fse/*"]
      },
      {
        # export-aws DeleteObjects; the research sync never uses --delete.
        Sid      = "ExportDelete"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = ["${local.bucket}/fse/*"]
      },
      {
        # export-aws verifies the bucket settings; it never changes them.
        Sid    = "ExportBucketSettings"
        Effect = "Allow"
        Action = [
          "s3:GetBucketPolicy",
          "s3:GetLifecycleConfiguration",
          "s3:GetEncryptionConfiguration",
          "s3:GetBucketPublicAccessBlock",
          "s3:GetBucketOwnershipControls",
        ]
        Resource = [local.bucket]
      },
      {
        Sid    = "ExportCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:CreateDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
        ]
        Resource = [
          "${local.glue_arn}:catalog",
          "${local.glue_arn}:database/skylit_fse",
          "${local.glue_arn}:table/skylit_fse/*",
        ]
      },
    ]
  })
}
