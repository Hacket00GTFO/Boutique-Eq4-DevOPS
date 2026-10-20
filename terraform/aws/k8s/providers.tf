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

# Learner Lab aprovisiona roles EKS distintos con prefijos/sufijos propios de
# cada cuenta. EKS no permite usar el rol del cluster tambien para los nodos.
# one() falla si hay varios candidatos; sin ninguno, role_arn queda invalido.
data "aws_iam_roles" "eks_cluster" {
  name_regex = ".*-LabEksClusterRole-.*"
}

data "aws_iam_roles" "eks_nodes" {
  name_regex = ".*-LabEksNodeRole-.*"
}
