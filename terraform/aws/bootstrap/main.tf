# El bucket de state NO se crea con el recurso "aws_s3_bucket" de Terraform.
#
# Razon: en AWS Academy Learner Lab, una politica de la cuenta (SCP) niega
# el permiso "s3:GetBucketObjectLockConfiguration". El proveedor de AWS
# intenta leer esa configuracion automaticamente cada vez que gestiona un
# bucket S3 (en el create inicial y en cada plan/apply/refresh despues),
# asi que cualquier bucket manejado como recurso de Terraform en esta cuenta
# queda roto de forma permanente, no solo la primera vez.
#
# Por eso el bucket se crea a mano, una sola vez, con AWS CLI. Ver el
# README.md de esta carpeta para los comandos exactos.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}
