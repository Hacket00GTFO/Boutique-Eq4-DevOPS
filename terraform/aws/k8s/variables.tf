variable "region" {
  type        = string
  description = "Región de AWS. Learner Lab normalmente solo permite us-east-1."
  default     = "us-east-1"
}

variable "project_name" {
  type        = string
  description = "Prefijo usado para nombrar los recursos de este proyecto"
  default     = "boutique-eq4"
}

variable "cluster_name" {
  type        = string
  description = "Nombre del cluster EKS"
  default     = "boutique-eq4-eks"
}

variable "microservices" {
  type        = list(string)
  description = "Nombres de los microservicios (carpetas en src/) que necesitan su propio repositorio ECR"
  default = [
    "adservice",
    "cartservice",
    "catalogservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "loadgenerator",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "shippingservice",
  ]
}
