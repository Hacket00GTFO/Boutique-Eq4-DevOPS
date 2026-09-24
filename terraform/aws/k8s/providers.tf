terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Mismo bucket de state que el stack serverless (terraform/aws), pero con
  # su propia key: son dos stacks independientes a proposito, para poder
  # crear/destruir el cluster EKS (cobra mientras exista) sin tocar el
  # catalogo serverless. El nombre del bucket se pasa en "terraform init".
  backend "s3" {
    key    = "modulo-k8s/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

# En Learner Lab no se pueden crear roles IAM nuevos: reusamos el rol que ya
# existe en la cuenta (LabRole) tanto para el cluster EKS como para los
# nodos. Si el trust policy de LabRole no incluye eks.amazonaws.com y/o
# ec2.amazonaws.com, esto va a fallar al aplicar y hay que ajustarlo.
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}
