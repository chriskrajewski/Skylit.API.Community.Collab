# The fse data bucket. `fse export-aws` creates it (Glue/Athena export under
# fse/); this stack adopts it through the import block in ../../imports.tf and
# adds the hosting prefixes cache/, runs/ and locks/. The settings below match
# what fse export-aws sets, plus the runs/ transition. No versioning: the
# existing bucket has none and everything in it is rebuildable.
#
# The bucket holds licensed Skylit API data: it must stay private.

locals {
  bucket_arn = "arn:aws:s3:::${var.bucket_name}"
}

resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.bucket_arn, "${local.bucket_arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.data]
}

# Replaces the bucket's whole lifecycle configuration, so it repeats the
# abort-multipart rule fse export-aws created.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "runs-to-standard-ia"
    status = "Enabled"

    filter {
      prefix = "runs/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }
}
