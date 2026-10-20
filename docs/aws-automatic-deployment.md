# Despliegue automático al fusionar development en main

La ruta es **AWS Academy Learner Lab**. Una vez terminada la preparación inicial,
la renovación de una sesión requiere actualizar únicamente estos **repository
secrets** en GitHub, con los tres valores de la misma sesión:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN`

El laboratorio debe estar iniciado. GitHub no puede pulsar **Start Lab**,
prolongar su temporizador ni levantar un laboratorio que la plataforma canceló.
El sistema usa los recursos existentes; un reset que elimine EKS, Vault o sus
datos requiere recuperación de infraestructura. Esta instalación de un solo
nodo Vault no ofrece producción permanente ni alta disponibilidad.

## Qué ejecuta un merge

1. Un pull request del mismo repositorio, de `development` a `main`, debe quedar
   **merged**. Cerrar un PR sin fusionarlo, un PR de otra rama o un fork no
   inicia esta ruta. También se incluyen merges que solo cambien documentación.
2. `CI: Docker build & push a ECR` fija el SHA completo del merge, comprueba
   que pertenece a `main` y ejecuta las pruebas PostgreSQL y Vault sobre ese SHA.
3. Construye y publica los doce servicios con esa etiqueta inmutable en ECR.
   Todas las publicaciones deben terminar antes de continuar.
4. Llama al workflow reutilizable `CD: Helm deploy a EKS` con el mismo SHA.
5. Comprueba la preparación inicial, reanuda las instancias conocidas que estén
   detenidas y espera SSM. No crea instancias, cambia roles ni redimensiona EKS.
6. Ejecuta Ansible por SSM sobre la EC2 exacta de Vault. Conserva Raft y la
   configuración KMS. Recupera el certificado **público** por SSM autenticado.
7. Comprueba imágenes, CSI y nodos; abre el túnel SSM y verifica TLS.
8. GitHub presenta su identidad OIDC a Vault. Vault verifica repositorio y
   propietario por ID numérico, rama `main`, environment `vault-lab`, audiencia
   y workflow. Emite un token batch de lectura con duración máxima de 15 minutos.
9. Entrega las nueve claves al Secret, ejecuta Helm y migración y verifica
   login, catálogo y checkout. Ante fallo conserva la custodia de PostgreSQL
   y aplica la recuperación de la release definida en `deploy.py`.

Las ejecuciones se serializan; un merge nuevo no cancela un despliegue en curso.
No se guarda un token de Vault en GitHub. El certificado se obtiene durante cada
ejecución. Los tokens de operador, raíz y las participaciones de recuperación
permanecen fuera de GitHub Actions.

## Preparación inicial: se hace una sola vez

Esta sección amplía el [despliegue inicial](learner-lab-deployment.md). Presupone
EKS, ECR, CSI, namespace y un Vault inicializado con las nueve claves existentes.
No recrear esos recursos ni ejecutar `seed` sobre la base existente.

La migración requiere una ventana de mantenimiento de Vault. Mantener el
laboratorio activo durante toda la operación. Las comprobaciones aisladas de
JWT y migración no demuestran que el Lab real permita KMS: hay que comprobarlo.

### 1. Activar la sesión y comprobar permisos

En Ubuntu/WSL, cargar las tres credenciales de **AWS Details** mediante prompts
ocultos y exportarlas. Luego, desde la raíz del repositorio actualizado:

```bash
source .venv/bin/activate
export AWS_DEFAULT_REGION=us-east-1
export AWS_REGION=us-east-1
export AWS_PAGER=""
python3 scripts/vault/automation_preflight.py
```

El preflight consulta identidad, permiso real de `DescribeCluster` y simulación
IAM para KMS y arranque de EC2. STS por sí solo puede responder aunque la política
`voc-cancel-cred` haya cancelado la sesión. La simulación es orientativa; la
creación real de la clave y el acceso desde el perfil de Vault deben funcionar.
Una denegación del Lab exige resolver esa capacidad con su administrador; no
se retiran las restricciones IAM para continuar.

### 2. Crear y conservar la clave KMS

En el archivo local **no versionado** `terraform/aws/vault/terraform.tfvars`,
conservar los valores existentes y establecer:

```hcl
enable_auto_unseal = true
```

No dejar otra asignación del mismo nombre. Con el nombre del bucket de estado
exportado en `TF_STATE_BUCKET`:

```bash
terraform -chdir=terraform/aws/vault init -backend-config="bucket=$TF_STATE_BUCKET"
terraform -chdir=terraform/aws/vault plan -out=/tmp/boutique-vault-kms.tfplan
```

Revisar el plan: debe añadir la clave KMS, sin reemplazar EC2 ni borrar recursos.
Solo después de esa revisión:

```bash
terraform -chdir=terraform/aws/vault apply /tmp/boutique-vault-kms.tfplan
VAULT_KMS_KEY_ARN="$(terraform -chdir=terraform/aws/vault output -raw auto_unseal_key_arn)"
export VAULT_KMS_KEY_ARN
```

La política de la clave permite al rol del perfil existente de Vault
`Encrypt`, `Decrypt` y `DescribeKey`; el servidor obtiene credenciales temporales
del perfil EC2. No se copian claves del usuario al servidor ni se crean roles IAM.
El recurso tiene `prevent_destroy`. **Después de migrar no cambiar el flag a
false, deshabilitar, programar borrado ni sustituir la clave.** Conservar su ARN
con los backups: las participaciones de recuperación no sustituyen una clave
KMS perdida. La clave tiene coste propio de AWS.

### 3. Actualizar Ansible y abrir la conexión privada

Usar el ID real de Vault y el bucket dedicado SSM, distinto del estado Terraform:

```bash
export VAULT_INSTANCE_ID=i-024a5c1e3a2a7fc75
export ANSIBLE_SSM_BUCKET=boutique-vault-ssm-716142453772
python3 scripts/vault/resume_lab.py --instance-id "$VAULT_INSTANCE_ID"
python3 -m pip install -r ansible/requirements.txt
(cd ansible && ansible-galaxy collection install -r requirements.yml)
(cd ansible && ansible-playbook site.yml --limit "$VAULT_INSTANCE_ID")
```

Este primer cambio del servicio/certificado puede reiniciar Vault y dejarlo
sellado todavía con Shamir. El paso de migración admite ese estado y usa el quorum
local. El servicio pasa a leer `/etc/vault.d/` para conservar `seal.hcl` separado.
Los SAN usan localhost e IP privada; un cambio de IP pública ya no obliga a
regenerar el certificado. Para la UI usar el túnel.

Si ya existe un túnel sano, reutilizarlo. Si no existe, iniciar uno en un directorio
privado nuevo; no iniciar dos supervisores sobre el mismo puerto:

```bash
umask 077
export VAULT_ADDR=https://localhost:18200
export VAULT_CACERT="$PWD/.vault-lab/certs/$VAULT_INSTANCE_ID.pem"
TUNNEL_RUN_DIR="$(mktemp -d "$PWD/.vault-lab/tunnel.XXXXXXXX")"
nohup python3 -u scripts/vault/ssm_tunnel.py \
  --instance-id "$VAULT_INSTANCE_ID" \
  --pid-file "$TUNNEL_RUN_DIR/supervisor.pid" \
  >"$TUNNEL_RUN_DIR/tunnel.log" 2>&1 </dev/null &
```

### 4. Migrar el Vault existente y demostrar auto-unseal

Se necesitan el archivo privado `~/vault-custody/init.json`, las claves GPG y
las contraseñas de los custodios **2 y 3**, ya verificadas en esta instalación,
y un operador válido en `~/vault-custody/operator.json`. El operador dura 24
horas; si expiró, recuperar uno mediante la ceremonia controlada antes de migrar.
No subir estos archivos, contraseñas ni participaciones a GitHub.

```bash
set +x
export GPG_TTY="$(tty)"
KMS_SNAPSHOT="$HOME/vault-custody/before-kms-$(date -u +%Y%m%d-%H%M%S).snap"
python3 scripts/vault/migrate_kms.py \
  --instance-id "$VAULT_INSTANCE_ID" \
  --key-arn "$VAULT_KMS_KEY_ARN" \
  --snapshot "$KMS_SNAPSHOT"
```

El helper descifra las participaciones en memoria, verifica el operador, guarda
un snapshot Raft privado **antes** de cambiar el sello, instala la configuración
KMS nativa y migra con quorum. Compara las nueve claves existentes antes/después
y reinicia otra vez sin presentar participaciones. Solo marca `verified` cuando
el segundo arranque se desbloquea automáticamente y conserva los valores.

El journal privado queda junto al snapshot, con sufijo `.migration.json`. Guardar
snapshot, journal y custodia fuera de Git con los backups. Si una operación se
interrumpe, conservar la misma ruta `KMS_SNAPSHOT`, la clave y `seal.hcl`; el helper
admite `--resume` sobre ese snapshot/journal. No restaurar automáticamente otro
snapshot, inicializar de nuevo ni quitar el sello como remedio.

### 5. Activar OIDC y GitHub una vez

Con `gh` autenticado como administrador, en la misma terminal con AWS exportado,
túnel y operador disponibles:

```bash
python3 scripts/vault/configure_actions.py \
  --repo Hacket00GTFO/Boutique-Eq4-DevOPS \
  --instance-id "$VAULT_INSTANCE_ID" \
  --expected-account 716142453772 \
  --ssm-bucket "$ANSIBLE_SSM_BUCKET" \
  --migration-journal "${KMS_SNAPSHOT%.snap}.migration.json"
```

El helper exige una migración verificada, configura la confianza OIDC y publica
las tres credenciales AWS mediante stdin. Elimina sus duplicados del environment
solo después de subir las tres al repositorio; esos duplicados podrían conservar
valores viejos y anular la renovación del repositorio. Elimina el `VAULT_CI_TOKEN`
heredado. Preserva las protecciones existentes y se detiene si hay reglas que
requieren tratamiento manual. Activa `VAULT_AUTOMATION_READY` al final.

| Ubicación | Configuración inicial |
| --- | --- |
| Repository secrets | Las tres credenciales AWS; `TF_STATE_BUCKET` se conserva para el workflow de datos |
| Repository variable | `AWS_ACCOUNT_ID` para validar renovaciones desde el helper |
| Environment `vault-lab`, limitado a `main` | `VAULT_INSTANCE_ID`, `VAULT_SSM_BUCKET`, `VAULT_AUTOMATION_READY=true` |
| Vault | Auth JWT `github`, rol `boutique-main`, política exacta `boutique-ci` |

`ENABLE_VAULT_LAB` y `VAULT_CA_CERT_B64` ya no controlan este CD. No copiar las
nueve claves de aplicación a GitHub. Una preparación parcial no acredita que
OIDC funcione en Actions: verificar también el primer merge y su smoke real.

## Cada sesión posterior: renovar solo las tres claves

1. **Start Lab** y esperar el indicador verde.
2. En **AWS Details**, tomar los tres valores nuevos de la misma sesión.
3. En GitHub, **Settings → Secrets and variables → Actions → Repository secrets**,
   actualizar los tres nombres indicados al principio. No crear copias dentro de
   `vault-lab`.
4. Fusionar el PR `development` → `main`. Seguir el build ECR y el job de deploy
   dentro de esa misma ejecución.

Como alternativa al paso 3, si ya cargaste los valores en la terminal Ubuntu y
`gh` sigue autenticado, el helper transfiere los tres sin imprimirlos y **sin
necesitar túnel ni token de operador**:

```bash
python3 scripts/vault/configure_actions.py \
  --repo Hacket00GTFO/Boutique-Eq4-DevOPS --rotate-aws-only
```

Cambiar un secret no dispara una ejecución. Para reintentar una release sin otro
merge, ejecutar manualmente `CI: Docker build & push a ECR` desde `main` (prueba,
construye y despliega), o `CD: Helm deploy a EKS` desde `main` con un SHA completo
de imágenes ya publicadas. Si expira la sesión en mitad del build/deploy, renovar
los tres valores y repetir; GitHub no puede renovar las credenciales del Lab.

## Comprobar y abrir la aplicación

En una terminal que tenga las credenciales nuevas:

```bash
aws eks update-kubeconfig --name boutique-eq4-eks --region us-east-1 &&
kubectl -n boutique-vault-lab get pods,pvc &&
kubectl -n boutique-vault-lab port-forward service/frontend 8080:80
```

Abrir `http://localhost:8080`. El frontend es privado; el port-forward mantiene
ocupada esa terminal hasta Ctrl+C. Un `AccessDenied` con `voc-cancel-cred` indica
que se debe reactivar el Lab y renovar las tres credenciales, no cambiar permisos.

## Referencias del mecanismo

- [Vault: JWT y restricciones de identidad](https://developer.hashicorp.com/vault/api-docs/auth/jwt).
- [Vault: sello AWS KMS y credenciales del perfil EC2](https://developer.hashicorp.com/vault/docs/configuration/seal/awskms).
- [Vault: migración de sellos y custodia de recuperación](https://developer.hashicorp.com/vault/docs/concepts/seal).
- [GitHub: identidad OIDC](https://docs.github.com/en/actions/reference/security/oidc).
