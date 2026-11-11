# Despliegue automático en AWS Learner Lab

## Operación habitual

Los cambios de código y de workflows se suben a `development`. La creación y
fusión del PR de `development` a `main` corresponde al responsable del
repositorio. Los pushes a `development` ejecutan CI sin desplegar la aplicación.

Con la preparación inicial verificada, cada merge de un PR del mismo repositorio
`development` → `main` prueba el SHA fusionado, publica sus doce imágenes en ECR
y despliega la aplicación en EKS. También se procesan merges de documentación.

1. Activar **Start Lab** y esperar que la sesión esté disponible.
2. Copiar los tres valores de **AWS Details** de la misma sesión a los
   **Repository secrets** de GitHub Actions:
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` y `AWS_SESSION_TOKEN`.
3. Fusionar el PR de `development` a `main` y seguir `CI: Docker build & push a ECR`.
4. Comprobar el job de CD y su smoke funcional, además del resumen de release.
   Un workflow con despliegue `skipped` no acredita un despliegue.

Las tres credenciales AWS se renuevan juntas. No crear copias en el environment
`vault-lab`: sus valores tendrían prioridad sobre los del repositorio. Vault
entrega las claves de aplicación mediante OIDC en cada job, con TLS verificado;
no es necesario renovar un token de Vault ni una variable con su certificado.

GitHub no puede activar ni extender el temporizador de AWS Academy. La sesión
debe mantenerse vigente durante las pruebas, el build y el despliegue.
Actualizar un secret no inicia un workflow por sí solo.

## Preparación inicial de la instalación recuperada

Esta preparación se realiza una vez sobre la infraestructura existente. La copia
local aceptada contiene los productos y el administrador preservado; no incluye
los pedidos anteriores de AWS. Consultar el [informe de recuperación](recovery-2026-10-20.md).

El environment `vault-lab` permite únicamente la rama `main` y conserva estas
variables permanentes:

| Variable | Finalidad |
| --- | --- |
| `VAULT_INSTANCE_ID` | Instancia existente de Vault |
| `VAULT_SSM_BUCKET` | Bucket dedicado al transporte de Ansible por SSM |
| `VAULT_KMS_KEY_ARN` | Clave persistente de auto-unseal |
| `DATA_RECOVERY_VERIFIED` | Solo `true` tras verificar PostgreSQL |
| `VAULT_AUTOMATION_READY` | Solo `true` tras comprobar OIDC, TLS, KMS y custodia |

Mantener ambos indicadores en `false` mientras se completa la recuperación.
El secret temporal `LOCAL_RECOVERY_PAYLOAD` contiene la copia y configuración
aceptadas durante esta preparación; no forma parte de la operación habitual.

1. Incorporar las correcciones mediante PR y ejecutar manualmente
   `CI: Docker build & push a ECR`, desde **main**, con
   `operation=initialize-local-data`.
2. El job reactiva las instancias conocidas. Si no hay nodos registrados y
   `nodeadm-config` quedó fallido, comprueba los permisos actuales y reutiliza
   la recuperación controlada: únicamente los tres trabajadores conocidos,
   con sus roles esperados, sin nodos registrados y sin namespace de aplicación.
   Espera los nodos y EBS CSI,
   reutiliza la inicialización KMS verificada, abre SSM y espera su disponibilidad.
3. Comprueba TLS y auto-unseal antes de solicitar la identidad OIDC de GitHub.
   Restaura PostgreSQL y compara el dump, las tablas, los conteos y el administrador.
   Si encuentra datos sin procedencia verificada, se detiene sin sobrescribirlos.
4. Conserva el artefacto `local-bootstrap-verification`. Contiene custodia cifrada
   y comprobantes públicos; nunca incluye el dump ni los valores de aplicación.
   Se sube también si falla una etapa posterior y caduca a los siete días.
5. Con `gh` autenticado como administrador, desde Linux/WSL ejecutar:

```bash
python3 scripts/vault/configure_actions.py \
  --repo Hacket00GTFO/Boutique-Eq4-DevOPS \
  --activate-from-run ID_DE_LA_EJECUCION
```

El helper exige que el job de preparación haya terminado correctamente en
`main`, verifica la identidad del repositorio, el intento de ejecución, la
instancia, KMS, los comprobantes OIDC/datos y la integridad de la custodia.
Guarda una copia permanente en `~/vault-custody/actions-ID-INTENTO/` con permisos
privados antes de modificar GitHub. Elimina los secrets temporales y heredados,
retira las copias AWS del environment y activa los dos indicadores al final,
con lectura de comprobación. Las claves privadas PGP deben seguir bajo custodia.

Configurar la confianza OIDC con el modo anterior del helper ya no activa CD:
una configuración escrita no demuestra que GitHub pueda autenticarse.
El modo `--activate-from-run` usa la evidencia de la inicialización KMS nueva
sin requerir un token de operador ni volver a inicializar Vault.

Mantener la clave KMS habilitada y sus datos persistentes. Las participaciones de
recuperación no sustituyen una clave KMS perdida. Las instalaciones antiguas
con Shamir requieren completar su migración controlada antes de este proceso;
consultar [Vault y custodia](vault-lab.md).

## Controles de cada release

- El SHA de `main` debe corresponder exactamente a un PR fusionado desde
  `development` del mismo repositorio. Pushes directos y otras ramas no despliegan.
- Pruebas unitarias, integración PostgreSQL, seguridad de Vault y Helm preceden
  a la publicación. Las descargas Go tienen reintentos acotados; las pruebas
  fallidas no se convierten en éxitos mediante reintentos.
- Las colas conservan hasta 100 ejecuciones pendientes con `queue: max` y no
  cancelan el despliegue en curso. El bootstrap y CD comparten exclusión sobre
  el environment, distinta de la cola del workflow padre.
- Primero se esperan los tres nodos Ready, incluido el nodo de almacenamiento;
  después EBS CSI. SSM y Vault tienen plazos separados de 120 segundos.
- Si una sesión del Lab dejó los tres trabajadores sin registrar y
  `nodeadm-config` fallido, CD comprueba primero la identidad del PVC/PV/EBS
  restaurado, cuenta, VPC, grupos, roles y sus tres instancias en servicio.
  Comprueba por SSM que el trabajador de almacenamiento sigue sin kubelet ni
  containerd activos y que el permiso AWS actual funciona. Revalida los
  trabajadores, el volumen y la ausencia de nodos antes de solicitar un único
  reinicio de esas tres instancias. Si un nodo se registra, no solicita reinicios.
- El PVC, el volumen EBS y el esquema deben coincidir con la recuperación
  verificada. La ausencia de datos no inicia una base nueva automáticamente.
- Helm espera workloads y migraciones. El smoke prueba frontend, administrador,
  catálogo, gRPC, checkout y PostgreSQL con sus propios datos sintéticos.
- Ante fallo se intenta recuperar la release y el Secret anteriores y se prueba
  esa recuperación. Las migraciones deben ser compatibles con la versión previa:
  Helm no deshace cambios SQL. Conservar los respaldos y los PVC.

ECS es una ruta histórica manual. La capa Terraform/DynamoDB tiene su workflow
separado y conserva su configuración inicial `TF_STATE_BUCKET`.

## Reintentos y diagnóstico

### Identidad OIDC inmutable de este repositorio

La configuración pública de GitHub para este repositorio devuelve
`use_immutable_subject=true`. El rol de Vault debe exigir exactamente:

```text
repo:Hacket00GTFO@181441407/Boutique-Eq4-DevOPS@1333737803:environment:vault-lab
```

El formato anterior sin IDs provoca `invalid subject (sub) claim`. La corrección
conserva los controles de audiencia, IDs numéricos, rama `main`, environment,
workflows autorizados y tokens de CI de lectura con un máximo de quince minutos.
No cambiar la configuración de GitHub al formato anterior para eludir el fallo.
El formato se documenta en [OIDC de GitHub](https://docs.github.com/en/actions/reference/security/oidc).

Si Vault ya estaba inicializado con el subject anterior, actualizar el código
no modifica su rol existente: la inicialización conserva su estado verificado.
Se necesita una sesión de operador una sola vez. La actualización acepta
únicamente el rol del mismo repositorio y propietario, con los mismos IDs;
rechaza subjects ajenos y restricciones adicionales que pudieran perderse.
Consultar la [ceremonia de recuperación](vault-lab.md#recuperar-acceso-cuando-una-clave-pgp-no-se-abre).
Después repetir el bootstrap hasta comprobar el login desde GitHub, restaurar
PostgreSQL y activar mediante la evidencia de una ejecución exitosa.

### Prerrequisitos y ejecuciones manuales

En un merge normal, estos cinco jobs aparecen `skipped` por diseño:

| Job | Cuándo se ejecuta |
| --- | --- |
| `recovery-inventory` | Diagnóstico manual `operation=inventory` en development |
| `inspect-worker` | Diagnóstico o recuperación inicial manual en development |
| `prepare-vault-kms` | Preparación manual de KMS en development |
| `rebuild-infrastructure` | Reconstrucción inicial explícita en main |
| `initialize-local-data` | Restauración inicial explícita en main, antes de activar CD |

La recuperación acotada del arranque durante una release forma parte del job
CD; no reactiva la restauración inicial ni el job de reconstrucción.
`release-summary` propaga un fallo de despliegue para que el workflow completo
no declare éxito si la aplicación no pasó la entrega y el smoke.

Un `requirements` fallido con `Application data recovery is not verified`
indica que la preparación inicial sigue pendiente. Ese control ocurre antes de
autenticar en AWS; por sí solo no indica credenciales AWS inválidas. Un
`inspect-worker` aprobado acredita solo la consulta de diagnóstico. Si `build`
y `deploy` aparecen `skipped`, la aplicación no fue desplegada por esa ejecución.

Después de renovar las tres credenciales, iniciar una ejecución nueva desde
`main` con `operation=release` para probar, construir y desplegar. Para reutilizar
imágenes existentes, ejecutar `CD: Helm deploy a EKS` desde `main` con el SHA
completo. Los jobs de la nueva ejecución usarán los secrets actualizados.

El modo `inventory` en `development` consulta recursos sin modificarlos. Las
operaciones de preparación y reparación disponibles son explícitas; no forman
parte de un merge normal ni reconstruyen recursos eliminados automáticamente.

Los mensajes distinguen credenciales vencidas, permisos denegados, SSM no
conectado, puerto ocupado, timeout y error TLS. El cierre de SSM se ejecuta
siempre; si no puede confirmarse, conserva el identificador de sesión y un log
con diagnósticos saneados. No usar `tlsSkipVerify` ni copiar tokens en los logs.

La alternativa local para renovar únicamente los tres secrets sigue siendo:

```bash
python3 scripts/vault/configure_actions.py \
  --repo Hacket00GTFO/Boutique-Eq4-DevOPS --rotate-aws-only
```

Ese modo requiere las tres credenciales ya exportadas en la terminal y valida
acceso AWS antes de subirlas mediante stdin, sin imprimir sus valores.

## Abrir la aplicación y aceptar la entrega

El frontend conserva su acceso privado. Con credenciales vigentes:

```bash
aws eks update-kubeconfig --name boutique-eq4-eks --region us-east-1
kubectl -n boutique-vault-lab port-forward service/frontend 8080:80
```

Abrir `http://localhost:8080`. Para dar la automatización por verificada se
necesita un merge con CD y smoke aprobados, seguido de otro en una sesión
posterior renovando solo las credenciales AWS. Comprobar persistencia del
administrador y de los datos entre ambas ejecuciones y conservar sus enlaces.
