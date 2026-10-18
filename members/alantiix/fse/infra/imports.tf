# Adopts the bucket `fse export-aws` created. Never run terraform import by hand.
#
# Gated on adopt_existing_data_bucket (default true) because terraform test
# cannot import from a mock provider; tests that leave it true override the
# imported resource instead. Set it false only in an account where the bucket
# does not exist yet, so Terraform creates it.
import {
  for_each = var.adopt_existing_data_bucket ? toset([local.data_bucket_name]) : toset([])

  to = module.data_bucket.aws_s3_bucket.data
  id = each.value
}
