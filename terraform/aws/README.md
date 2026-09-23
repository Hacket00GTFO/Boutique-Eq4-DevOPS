# Infraestructura AWS (Learner Lab)

Esta carpeta es independiente del `terraform/` de la raíz (ese es de Google
Cloud / GKE). Aquí vive la infraestructura de AWS del proyecto, pensada para
correr dentro de un **AWS Academy Learner Lab**. Se divide en dos partes:

- **Esta carpeta (`terraform/aws/`)**: solo la **capa de datos** — hoy, la
  tabla DynamoDB de catálogo. Nada de cómputo vive aquí.
- **`terraform/aws/k8s/`**: el clúster EKS, ECR, y toda la parte de cómputo
  (contenedores). El backend del Admin Panel (catálogo, inventario, pedidos)
  y los 11 microservicios de Online Boutique corren ahí, empaquetados con
  Docker y desplegados vía Helm — no en Lambda. Ver el README de esa carpeta.

## Por qué hay una carpeta `bootstrap/`

Terraform necesita guardar su "state" (qué recursos ya creó) en algún lado
que no desaparezca entre sesiones. Usamos un bucket S3 para eso.

**Este bucket NO se crea con `terraform apply`.** En AWS Academy Learner Lab
hay una política de cuenta (SCP) que niega el permiso
`s3:GetBucketObjectLockConfiguration`. El proveedor de AWS intenta leer esa
configuración automáticamente cada vez que gestiona un bucket S3 — no solo
al crearlo, también en cada `plan`/`apply`/`refresh` después — así que
cualquier bucket S3 manejado como recurso de Terraform en esta cuenta queda
roto de forma permanente. Por eso el bucket se crea a mano, una sola vez,
con AWS CLI (mismo resultado, sin el problema).

## Paso 1: crear el bucket de state (una sola vez, con AWS CLI)

1. Inicia el Learner Lab ("Iniciar") y copia las 3 credenciales de "AWS Details".
2. Configura el AWS CLI (ver instrucciones más abajo si no lo has hecho):
   ```sh
   aws configure set aws_access_key_id "..."
   aws configure set aws_secret_access_key "..."
   aws configure set aws_session_token "..."
   aws configure set region us-east-1
   ```
3. Crea el bucket (cambia el nombre por uno único tuyo) y aplícale el mismo
   endurecimiento que hubiera hecho Terraform:
   ```sh
   BUCKET="boutique-eq4-tfstate-<algo-unico-tuyo>"

   aws s3api create-bucket --bucket "$BUCKET" --region us-east-1

   aws s3api put-bucket-versioning --bucket "$BUCKET" \
     --versioning-configuration Status=Enabled

   aws s3api put-bucket-encryption --bucket "$BUCKET" \
     --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

   aws s3api put-public-access-block --bucket "$BUCKET" \
     --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   ```
4. Guarda ese nombre de bucket — lo necesitas en el Paso 2.

Este paso normalmente solo se hace una vez en la vida del proyecto (o si el
bucket se borra). `bootstrap/` ya no tiene un recurso de Terraform que crear;
solo queda ahí por si en el futuro se necesita algo más de una sola vez.

## Paso 2: desplegar la capa de datos (hoy: la tabla de catálogo)

Cada vez que quieras aplicar cambios (con una sesión del Lab activa y credenciales frescas):

```sh
cd terraform/aws
terraform init -backend-config="bucket=<el-nombre-del-paso-1>"
terraform plan
terraform apply
```

Una vez aplicado, la tabla DynamoDB queda creada y disponible — sigue
existiendo aunque cierres el Learner Lab (es solo almacenamiento, no depende
de ningún cómputo prendido). Solo necesitas sesión activa para volver a
correr `terraform apply` (es decir, para desplegar cambios nuevos, como
agregar la tabla de inventario o de pedidos más adelante).

Para el cómputo que lee/escribe esta tabla (contenedores en el clúster EKS),
ver `terraform/aws/k8s/README.md`.

## Paso 3: dejar que GitHub Actions lo haga por ti

El workflow `.github/workflows/deploy-aws-backend.yaml` corre `terraform
apply` automáticamente en cada push a `main` que toque `terraform/aws/**`.
Para que funcione, hay que definir estos secrets en el repo (Settings >
Secrets and variables > Actions):

- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` — las 3
  credenciales de "AWS Details". Hay que **actualizarlas cada vez que
  reinicias la sesión del Lab**, si no, el workflow falla al autenticarse
  (pero lo que ya está desplegado sigue funcionando igual).
- `TF_STATE_BUCKET` — el nombre del bucket del Paso 1. Este **no cambia**,
  se define una sola vez.

Forma rápida de actualizar los 3 secrets que cambian, con GitHub CLI en vez
de la interfaz web:
```sh
gh secret set AWS_ACCESS_KEY_ID --body "..."
gh secret set AWS_SECRET_ACCESS_KEY --body "..."
gh secret set AWS_SESSION_TOKEN --body "..."
```
