data "aws_caller_identity" "current" {}

variable "enable_auto_unseal" {
  type        = bool
  default     = false
  description = "Create the persistent KMS key before the one-time Shamir migration. Never disable after migration."
}

resource "aws_kms_key" "vault" {
  count                   = var.enable_auto_unseal ? 1 : 0
  description             = "Boutique Learner Lab Vault auto-unseal; retain with Raft backups"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountKeyAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "VaultInstanceProfileUse"
        Effect    = "Allow"
        Principal = { AWS = data.aws_iam_instance_profile.existing.role_arn }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:DescribeKey"]
        Resource  = "*"
      }
    ]
  })
  lifecycle {
    prevent_destroy = true
  }
}

output "auto_unseal_key_arn" {
  value = try(aws_kms_key.vault[0].arn, null)
}
