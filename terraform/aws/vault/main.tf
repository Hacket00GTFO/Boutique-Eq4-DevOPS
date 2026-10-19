data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

data "aws_subnet" "selected" {
  id = var.subnet_id
}

data "aws_iam_instance_profile" "existing" {
  name = var.instance_profile
}

resource "aws_security_group" "vault" {
  name_prefix = "boutique-vault-lab-"
  description = "Vault HTTPS only; administration and CI use SSM, never SSH"
  vpc_id      = var.vpc_id
  ingress {
    description = "Restricted administrative UI"
    from_port   = 8200
    to_port     = 8200
    protocol    = "tcp"
    cidr_blocks = var.admin_cidrs
  }
  egress {
    description = "SSM, S3 and signed distribution downloads"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "vault" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.vault.id]
  iam_instance_profile        = data.aws_iam_instance_profile.existing.name
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = 20
  }
  tags = { Name = "boutique-vault-lab" }
  lifecycle {
    precondition {
      condition     = data.aws_subnet.selected.vpc_id == var.vpc_id
      error_message = "The selected subnet must belong to the existing EKS VPC."
    }
    # Refreshing the public AMI parameter must not replace a live Vault.
    ignore_changes = [ami]
  }
}
