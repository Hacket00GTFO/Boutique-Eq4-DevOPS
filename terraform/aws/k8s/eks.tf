# El "cerebro" del cluster: la API de Kubernetes administrada por AWS.

resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = data.aws_iam_role.lab_role.arn
  version  = "1.31"

  vpc_config {
    subnet_ids = [aws_subnet.public_a.id, aws_subnet.public_b.id]
  }
}

# Por defecto, AWS limita a 1 "salto" de red el acceso de una instancia EC2
# a su propio servicio de metadatos (de ahi salen las credenciales de
# LabRole). Un proceso dentro de un pod esta a 2 saltos del host, asi que
# sin esto boto3 dentro de los contenedores nunca encuentra credenciales
# (NoCredentialsError). Subimos el limite a 2 para que los pods si puedan.
resource "aws_launch_template" "nodes" {
  name_prefix   = "${var.cluster_name}-nodes-"
  instance_type = "t3.medium"

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
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

  launch_template {
    id      = aws_launch_template.nodes.id
    version = aws_launch_template.nodes.latest_version
  }

  scaling_config {
    desired_size = 2
    min_size     = 1
    max_size     = 3
  }

  depends_on = [aws_eks_cluster.main]
}
