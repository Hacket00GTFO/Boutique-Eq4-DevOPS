# Puesta en marcha completa en AWS Academy Learner Lab

Esta lista ordena el despliegue de EKS, ECR, Vault, Ansible y Helm del proyecto.
La [guía de Vault](vault-lab.md) contiene los comandos de instalación, custodia,
rotación y recuperación. Vault funciona en un solo nodo en este laboratorio; no
ofrece alta disponibilidad ni un servicio de producción permanente.

## Estado y datos que faltan antes de ejecutar

- [ ] Sesión Learner Lab activa y las tres credenciales temporales de la **misma**
  sesión disponibles en el controlador Linux/WSL y como secrets de GitHub:
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`.
- [ ] Declarar `TF_STATE_BUCKET` como secret de GitHub. Confirmar que el bucket
  existe y conocer su **nombre** para los
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

La ruta principal actual es el [despliegue directo a AWS](aws-automatic-deployment.md):
las renovaciones solo requieren las tres claves AWS del repositorio y conservan
el Secret de aplicación existente en Kubernetes. Las fases de Vault/Ansible de
esta guía describen la preparación y operación manual; ya no bloquean el CD.

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
   ECR existen y que el grupo `boutique-eq4-eks-storage` está `ACTIVE`.
   Ese grupo usa `LabRole` en un nodo separado y con taint: el Lab no permite
   crear roles IAM, adjuntar la política EBS al rol de los nodos normales ni
   adaptar la confianza de `LabRole` para EKS Pod Identity. `LabRole` tiene
   permisos amplios; por ello solo el controlador EBS CSI debe ejecutarse en
   ese nodo. Consultar el esquema de configuración del addon antes de fijar
   allí el controlador mediante `nodeSelector` y `tolerations`. Los nodos de
   la aplicación conservan `LabEksNodeRole`. Esta es una adaptación del Lab;
   el taint evita colocaciones accidentales, pero no es un límite de seguridad.
   Fuera del Lab se debe usar un rol de Pod Identity limitado al CSI.
5. Instalar el addon `aws-ebs-csi-driver`, comprobar que está `ACTIVE` y probar
   un PVC real antes de Helm. PostgreSQL requiere una StorageClass funcional;
   el modo Vault selecciona explícitamente `boutique-ebs-gp3` con provisioner
   `ebs.csi.aws.com` y `WaitForFirstConsumer`, sin requerir una clase predeterminada.
   La clase heredada `gp2` con provisioner `kubernetes.io/aws-ebs` no demuestra
   que CSI funcione. Crear el namespace `boutique-vault-lab` si falta. La
   [guía de Vault](vault-lab.md#1-preparar-el-laboratorio) muestra las
   comprobaciones de CSI y namespace.

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
   **público** en el controlador. Hacer `init` con las claves PGP de los
   custodios, `unseal` con el quorum, `configure` y `seed` según
   [la ceremonia documentada](vault-lab.md#3-ceremonia-de-bootstrap-y-custodia).
3. Emitir el token de operador. Para ensayos manuales se puede emitir un token
   CI de lectura al KV exacto, con máximo de cuatro horas. El CD directo usa el
   Secret ya instalado en Kubernetes. El token raíz,
   las participaciones de unseal y el token de operador quedan fuera de GitHub.

**Puerta:** Vault responde por HTTPS con TLS verificado, está desprecintado y
contiene las nueve claves esperadas. La configuración CI del repositorio **no**
ejecuta `ansible-playbook site.yml` sobre la EC2: esta fase es manual.

### 4. Secrets y variables de GitHub

En el repositorio GitHub, abrir **Settings → Secrets and variables → Actions**.
En **Repository secrets**, actualizar los tres valores de la misma sesión del
Learner Lab de la cuenta **716142453772**. El CD directo no usa el environment
heredado `vault-lab`, certificados ni tokens de Vault.

| Ubicación | Nombre | Cuándo se necesita |
| --- | --- | --- |
| Secrets de repositorio | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | ECR y EKS; renovarlos juntos con cada sesión del Lab. |
| Secret de repositorio | `TF_STATE_BUCKET` | Workflow de la capa de datos; el usuario ya indicó que está declarado. |

No configurar las nueve credenciales de aplicación como GitHub secrets: el CD
directo conserva sus valores del Secret original de Kubernetes. La custodia de
Vault se opera mediante el procedimiento manual.

La guía de [conexión directa](aws-automatic-deployment.md) indica la ubicación
exacta de los secrets, el trigger y los requisitos existentes. El helper antiguo
`scripts/vault/configure_actions.py` pertenece al CD heredado y no prepara esta
ruta; no se requiere ejecutarlo para renovar las tres claves AWS.

### 5. Merge, imágenes y Helm

1. Resolver el check heredado de GKE si la protección de `main` lo exige, y
   fusionar `development` en `main`. El historial de ejecuciones fallidas seguirá
   visible; nuevas ejecuciones usan los workflows de `main` actualizados.
2. El PR debe proceder de `development` del mismo repositorio. También activa
   el pipeline si solo cambia documentación. Se admite ejecución manual de
   **CI: Docker build & push a ECR** desde `main`.
3. Verificar primero las pruebas PostgreSQL/Vault y luego la publicación de
   imágenes etiquetadas con el SHA del merge. Tras publicarlas todas, el pipeline
   llama al CD reutilizable **CD: Helm deploy a EKS** con el mismo SHA:
   verifica la cuenta, reanuda los nodos EKS existentes, comprueba ECR/CSI,
   lee el Secret original de Kubernetes,
   despliega Helm y ejecuta el smoke funcional. El frontend queda privado en
   `boutique-vault-lab`; acceder con el port-forward de
   [la guía](vault-lab.md#4-activar-github-actions).

**Puerta final:** build ECR y CD de EKS exitosos, Helm sano, pods listos,
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
- Operación manual de Vault/SSM falla: la [guía de Vault](vault-lab.md)
  documenta recuperación y custodia. El CD directo utiliza el Secret de Kubernetes.
- Sesión del Lab caducada: iniciar el Lab y actualizar las tres claves AWS del
  repositorio antes de reejecutar. El CD directo no necesita renovar un token Vault.

Esta lista se comprueba para cada cuenta y sesión del laboratorio. Registrar las
salidas reales de Terraform, Ansible, almacenamiento, ECR y CD en la matriz de
evidencias; una validación local del código no acredita el despliegue remoto.
