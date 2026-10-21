output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "vault_subnet_id" {
  value = aws_subnet.public_a.id
}

output "cluster_endpoint" {
  value = aws_eks_cluster.main.endpoint
}

output "storage_controller_node_group" {
  value = aws_eks_node_group.storage_controller.node_group_name
}

# Prefijo comun para el "images.repository" del Helm chart. Cada servicio
# vive en <ecr_registry>/<nombre-del-servicio>.
output "ecr_registry" {
  value = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/${var.project_name}"
}

output "ecr_repository_urls" {
  value = { for name, repo in aws_ecr_repository.services : name => repo.repository_url }
}
