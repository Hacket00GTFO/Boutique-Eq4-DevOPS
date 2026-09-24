# Un repositorio ECR por microservicio. ECR permite nombres con "/" como
# namespacing (ej. "boutique-eq4/frontend"), por eso el Helm chart solo
# necesita un unico prefijo (images.repository) + el nombre de cada servicio,
# igual que ya funciona hoy con Google Artifact Registry.
resource "aws_ecr_repository" "services" {
  for_each = toset(var.microservices)

  name         = "${var.project_name}/${each.value}"
  force_delete = true # permite borrar el repo aunque tenga imagenes adentro
}
