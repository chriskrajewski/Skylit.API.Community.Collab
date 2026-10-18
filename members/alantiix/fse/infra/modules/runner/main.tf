# Live runner host: one t4g.small (Amazon Linux 2023, arm64) running the fse
# image as fse-runner.service. No SSH key and no inbound rule: operators use
# SSM Session Manager. Secrets come from SSM Parameter Store at service start.
#
# Phase 2 seam: the web UI becomes a second systemd unit (and container) on
# this host; nothing here changes for it yet.

data "aws_ssm_parameter" "ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  log_group_name = "/fse/runner"
  registry_host  = split("/", var.ecr_repository_url)[0]

  # Read by the systemd units (EnvironmentFile) and so by every host script.
  host_env = join("\n", [
    "AWS_REGION=${var.region}",
    "AWS_DEFAULT_REGION=${var.region}",
    "FSE_ALERT_TOPIC_ARN=${var.alerts_topic_arn}",
    "FSE_IMAGE_REPO=${var.ecr_repository_url}",
    "FSE_FAULT_BACKOFF_S=${var.fault_backoff_s}",
    "FSE_LOG_GROUP=${local.log_group_name}",
    "FSE_SSM_PREFIX=${var.ssm_prefix}",
  ])

  docker_config = jsonencode({
    credHelpers = { (local.registry_host) = "ecr-login" }
  })

  user_data = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    host_env       = local.host_env
    docker_config  = local.docker_config
    data_volume_id = aws_ebs_volume.data.id
    fetch_secrets  = chomp(file("${path.module}/files/fse-fetch-secrets.sh"))
    alert          = chomp(file("${path.module}/files/fse-alert.sh"))
    mount_data     = chomp(file("${path.module}/files/fse-mount-data.sh"))
    start          = chomp(file("${path.module}/files/fse-start.sh"))
    runner_unit    = chomp(file("${path.module}/files/fse-runner.service"))
    alert_unit     = chomp(file("${path.module}/files/fse-runner-alert.service"))
  })
}

resource "aws_cloudwatch_log_group" "runner" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.ami.insecure_value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = aws_iam_instance_profile.runner.name
  monitoring             = false

  # Hop limit 1: only host processes (dockerd, the ECR helper, the aws CLI)
  # reach IMDS; the runner container never needs instance credentials.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size_gb
    encrypted   = true
  }

  user_data                   = local.user_data
  user_data_replace_on_change = false

  tags = {
    Name          = var.name
    Role          = var.name
    "Patch Group" = var.patch_group
  }

  lifecycle {
    # A new AL2023 AMI must not replace the host; SSM patching keeps it current.
    ignore_changes = [ami]
  }
}

# Live state, recordings, runs and the bar cache. Survives instance
# replacement; DLM snapshots it daily (patching.tf).
resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  type              = "gp3"
  size              = var.data_volume_size_gb
  encrypted         = true

  tags = {
    Name     = "${var.name}-data"
    Snapshot = "fse-data"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.this.id
  stop_instance_before_detaching = true
}

# Stable public IP (Phase 2 seam: allow-listing, a UI DNS record).
resource "aws_eip" "runner" {
  domain = "vpc"
  tags   = { Name = var.name }
}

resource "aws_eip_association" "runner" {
  allocation_id = aws_eip.runner.id
  instance_id   = aws_instance.this.id
}
