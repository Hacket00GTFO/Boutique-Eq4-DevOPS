# Puesta en marcha completa en AWS Academy Learner Lab

Esta lista ordena el despliegue de EKS, ECR, Vault, Ansible y Helm del proyecto.
La [guía de Vault](vault-lab.md) contiene los comandos de instalación, custodia,
rotación y recuperación. Este entorno es un laboratorio de un solo nodo; no
ofrece alta disponibilidad ni un servicio de producción permanente.

## Estado y datos que faltan antes de ejecutar

- [ ] Sesión Learner Lab activa y las tres credenciales temporales de la **misma**
  sesión disponibles en el controlador Linux/WSL y como secrets de GitHub:
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`.
- [x] El usuario indicó que `TF_STATE_BUCKET` ya está declarado como secret de
  GitHub. Confirmar que el bucket existe y conocer su **nombre** para los
  comandos manuales de Terraform; el secret de Actions no llega por sí solo a
  la terminal local. No registrar aquí su valor.
- [ ] Confirmar en la cuenta del Lab si ya existen el clúster
  `boutique-eq4-eks`, los repositorios ECR y la capa de datos. No aplicar un
  stack sobre recursos creados fuera de su estado de Terraform.
- [ ] Para un clúster nuevo, Terraform solicita EKS 1.35 en soporte estándar
  y los flujos usan Helm 3.22.1, compatible con esa versión de Kubernetes.
- [ ] Obtener VPC, subnet, perfil de instancia permitido por el Lab y la IP
  pública administrativa en CIDR `/32` para la EC2 de Vault. Preparar tres
  custodios PGP y una clave PGP distinta para el token raíz.
- [ ] Usar un controlador Ubuntu 26.04 x86_64, nativo o WSL, con Python 3.14 y las herramientas
  y versiones de la [guía de Vault](vault-lab.md#1-preparar-el-laboratorio).
  Conservar secretos y archivos de custodia fuera de Git.

**No activar todavía `ENABLE_VAULT_LAB`.** Ese valor elige el job de Vault en
lugar del despliegue Helm normal; con Vault aún sin instalar, el CD fallaría.

## Secuencia con puertas de verificación

### 1. Terraform: base AWS y EKS/ECR

1. Verificar la identidad de la sesión con `aws sts get-caller-identity` y
   confirmar que la región es `us-east-1`.
2. Inspeccionar primero el estado de `terraform/aws` (capa de datos) y
   `terraform/aws/k8s` (VPC, EKS y ECR). Inicializar cada stack con el mismo
   bucket de estado, pero con su propia clave S3; revisar `plan` antes de
   aplicar. La carpeta raíz `terraform/` es heredada de GCP y no forma parte
   de esta ruta AWS.
   En Learner Lab, confirmar que existen los roles distintos
   `LabEksClusterRole` y `LabEksNodeRole` (sus nombres completos incluyen
   prefijos y sufijos variables). El stack descubre cada uno por nombre; si
   falta alguno o aparecen varios candidatos, detenerse antes de `apply`.
3. Si EKS/ECR no existen, aplicar `terraform/aws/k8s` desde el controlador:

   ```bash
   terraform -chdir=terraform/aws/k8s init -backend-config="bucket=$TF_STATE_BUCKET"
   terraform -chdir=terraform/aws/k8s plan
   terraform -chdir=terraform/aws/k8s apply
   aws eks update-kubeconfig --name boutique-eq4-eks --region us-east-1
   ```

4. Comprobar que el clúster y sus nodos están operativos, que los repositorios
   ECR existen y que el addon `aws-ebs-csi-driver` está `ACTIVE`. PostgreSQL
   también requiere una StorageClass predeterminada funcional. Crear el
   namespace `boutique-vault-lab` si falta. La [guía de Vault](vault-lab.md#1-preparar-el-laboratorio)
   muestra las comprobaciones de CSI y namespace.

**Puerta:** EKS responde, ECR está listo, CSI y StorageClass funcionan. El
workflow de GitHub no crea automáticamente el stack `terraform/aws/k8s`.

### 2. Terraform: EC2 independiente para Vault

1. Obtener la VPC/subnet del EKS real. Elegir un bucket S3 **separado** para
   transferencias Ansible/SSM; no reutilizar el bucket de estado.
2. Ejecutar `scripts/vault/aws_prepare.py` con el bucket SSM, VPC y subnet.
   Completar localmente `terraform/aws/vault/terraform.tfvars` a partir del
   ejemplo; este archivo no se versiona. Revisar `plan` y aplicar el stack
   `terraform/aws/vault` con `TF_STATE_BUCKET`, siguiendo la
   [guía de Vault](vault-lab.md#1-preparar-el-laboratorio).
3. Esperar a que la EC2 etiquetada `boutique-vault-lab` aparezca `Online` en
   Systems Manager. Registrar el ID de instancia para el environment de GitHub.

**Puerta:** EC2 accesible mediante SSM, sin abrir SSH público. Si faltan permisos
del perfil IAM del Lab, resolverlos antes de continuar.

### 3. Ansible, TLS y custodia de Vault

1. Instalar los requisitos y colecciones Ansible desde `ansible/` y ejecutar
   `site.yml` por SSM. Repetirlo y exigir `changed=0`, `failed=0` y
   `unreachable=0` con `scripts/vault/check_idempotence.py`.
2. Abrir el túnel SSM, comprobar el certificado TLS y guardar su certificado
   **público** para el environment. Hacer `init` con las claves PGP de los
   custodios, `unseal` con el quorum, `configure` y `seed` según
   [la ceremonia documentada](vault-lab.md#3-ceremonia-de-bootstrap-y-custodia).
3. Emitir el token de operador y, al final, un token CI de lectura al KV exacto
   de la aplicación. El token CI dura como máximo cuatro horas. El token raíz,
   las participaciones de unseal y el token de operador quedan fuera de GitHub.

**Puerta:** Vault responde por HTTPS con TLS verificado, está desprecintado y
contiene las nueve claves esperadas. La configuración CI del repositorio **no**
ejecuta `ansible-playbook site.yml` sobre la EC2: esta fase es manual.

### 4. Secrets y variables de GitHub

En el repositorio GitHub, abrir **Settings → Secrets and variables → Actions**.
En **Secrets**, usar **New repository secret** para las credenciales AWS y el
bucket de estado; en **Variables**, usar **New repository variable** para el
interruptor, pero dejarlo sin activar hasta superar las puertas anteriores.
Luego abrir **Settings → Environments → vault-lab** (crearlo si no existe),
restringirlo a `main` y usar sus secciones **Environment secrets** y
**Environment variables**. Configurar:

| Ubicación | Nombre | Cuándo se necesita |
| --- | --- | --- |
| Secrets de repositorio o environment | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | ECR, EKS y conexión SSM; renovarlos con cada sesión del Lab. |
| Secret de repositorio | `TF_STATE_BUCKET` | Workflow de la capa de datos; el usuario ya indicó que está declarado. |
| Variables del environment `vault-lab` | `VAULT_INSTANCE_ID`, `VAULT_CA_CERT_B64` | EC2 y certificado público del Vault instalado. |
| Secret del environment `vault-lab` | `VAULT_CI_TOKEN` | Lectura del KV durante CD; emitirlo al final y renovarlo antes de expirar. |
| Variable de repositorio | `ENABLE_VAULT_LAB=true` | **Activar únicamente después de superar las tres puertas anteriores.** |

No configurar las nueve credenciales de aplicación como GitHub secrets: viven
en Vault y el CD las entrega al Secret de Kubernetes durante el despliegue.

### 5. Merge, imágenes y Helm

1. Resolver el check heredado de GKE si la protección de `main` lo exige, y
   fusionar `development` en `main`. El historial de ejecuciones fallidas seguirá
   visible; nuevas ejecuciones usan los workflows de `main` actualizados.
2. Para que el build ECR arranque por el merge, el commit debe tocar una de las
   rutas filtradas en `.github/workflows/ci-docker-ecr.yaml`. Si no arranca,
   lanzar manualmente **CI: Docker build & push a ECR** desde `main`.
3. Verificar primero las pruebas PostgreSQL/Vault y luego la publicación de
   imágenes etiquetadas con el SHA. El CD **CD: Helm deploy a EKS** se dispara
   tras un ECR exitoso. Con `ENABLE_VAULT_LAB=true` ejecuta
   `vault-lab-deploy`: verifica ECR/CSI, abre SSM, lee Vault, crea el Secret,
   despliega Helm y ejecuta el smoke funcional. El frontend queda privado en
   `boutique-vault-lab`; acceder con el port-forward de
   [la guía](vault-lab.md#4-activar-github-actions).

**Puerta final:** build ECR y CD de Vault exitosos, Helm sano, pods listos,
PostgreSQL persistente y smoke de login, catálogo y checkout satisfactorio.

### 6. Panel de operaciones y evidencia

El panel de Vault/Ansible puede mostrar vistas vacías aunque la aplicación esté
desplegada: hay que conectar el colector privado al backend y ejecutar Ansible
con el callback configurado. Seguir [operación del panel](admin-operations.md)
para publicar salud real de Vault y recaps, sin publicar tokens ni salidas
privadas. Registrar los resultados remotos en la
[matriz de evidencias](vault-evidence.md); las pruebas locales existentes no
acreditan por sí solas un despliegue AWS.

## Si una puerta falla

- Error al configurar AWS en Actions: comprobar que las tres credenciales son
  de la misma sesión activa del Learner Lab y actualizarlas juntas.
- EKS/Helm falla: revisar el paso concreto, el addon CSI, la StorageClass,
  capacidad de nodos y las imágenes ECR antes de repetir. No borrar PVC ni
  Secret para “empezar de cero”.
- Vault/SSM falla: comprobar instancia `Online`, túnel, TLS, unseal y vigencia
  del token CI. La [guía de Vault](vault-lab.md)
  documenta la recuperación y el rollback.
- Sesión del Lab o token CI caducados: renovar ambos antes de reejecutar el CD.

**Estado al preparar esta guía:** aquí no había credenciales AWS cargadas,
identificadores reales de red ni `terraform.tfvars` de Vault. No se creó
infraestructura ni se verificó el estado remoto de GitHub Secrets o EKS.
