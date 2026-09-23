# catalogservice

Backend del catálogo del Admin Panel. Expone una API HTTP (Flask) y lee/escribe
en la tabla DynamoDB `boutique-eq4-productos` (creada en `terraform/aws/`).

## Autenticación con AWS

El contenedor no lleva ninguna credencial de AWS configurada a mano. Cuando
corre dentro de un nodo del clúster EKS, hereda automáticamente los permisos
del rol IAM de ese nodo (`LabRole`) a través del servicio de metadatos de la
instancia EC2 — el mismo mecanismo que usaría cualquier proceso corriendo
directo en esa máquina. Esto es distinto de "IRSA" (IAM Roles for Service
Accounts), el método más moderno/recomendado en producción, que requeriría
crear un proveedor OIDC e IAM roles nuevos — no permitido en AWS Academy
Learner Lab.

## Variables de entorno

| Variable | Para qué |
|---|---|
| `TABLE_NAME` | Nombre de la tabla DynamoDB a usar |
| `AWS_REGION` | Región de AWS (`us-east-1`) |
| `PORT` | Puerto HTTP (default `8080`, lo fija Kubernetes vía el Helm chart) |

## Rutas

| Ruta | Método | Para qué |
|---|---|---|
| `/healthz` | GET | Liveness/readiness probe de Kubernetes |
| `/catalogo` | GET | Cuenta los productos en la tabla (placeholder — el CRUD real es el Módulo 1) |

## Correrlo local (fuera de Kubernetes)

```sh
pip install -r requirements.txt
export TABLE_NAME=boutique-eq4-productos
export AWS_REGION=us-east-1
python main.py
```

Necesita credenciales de AWS válidas en el entorno (ver `aws configure` en el
README de `terraform/aws/`).
