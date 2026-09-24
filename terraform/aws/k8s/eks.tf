# El "cerebro" del cluster: la API de Kubernetes administrada por AWS.
# No corre workloads por si sola, solo coordina.
resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = data.aws_iam_role.lab_role.arn
  version  = "1.31"

  vpc_config {
    subnet_ids = [aws_subnet.public_a.id, aws_subnet.public_b.id]
  }
}

# Las maquinas (EC2) donde realmente corren los pods/contenedores.
# NOTA: en Learner Lab, las instancias EC2 se apagan solas al terminar la
# sesion del Lab (4h) — a diferencia de la tabla DynamoDB, esto SI deja de
# responder cuando cierras el Lab, hasta que se vuelva a levantar.
resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.cluster_name}-nodes"
  node_role_arn   = data.aws_iam_role.lab_role.arn
  subnet_ids      = [aws_subnet.public_a.id, aws_subnet.public_b.id]
  instance_types  = ["t3.medium"]

  scaling_config {
    desired_size = 2
    min_size     = 1
    max_size     = 3
  }

  depends_on = [aws_eks_cluster.main]
}
