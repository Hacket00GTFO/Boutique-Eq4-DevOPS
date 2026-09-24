terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # El bucket se pasa en tiempo de "terraform init" (no se puede usar una
  # variable aquí para el bucket), por eso ese queda vacío. La region del
  # backend de S3 es independiente del provider "aws" de abajo, por eso se
  # repite aquí explícitamente. Ver README.md de esta carpeta.
  backend "s3" {
    key    = "modulo-catalogo/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.region
}
