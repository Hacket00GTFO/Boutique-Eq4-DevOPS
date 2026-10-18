# Portafolio de evidencias: Fase 3

Fecha: 18 de octubre de 2026, America/Mexico_City.

La implementación y las verificaciones locales no equivalen a una ejecución
en AWS. No hay credenciales AWS configuradas en este entorno ni un contexto
EKS activo. No se provisionaron recursos, publicaron cambios ni ejecutaron
pruebas destructivas remotas. Este documento mantiene explícita esa diferencia.

## Matriz requisito → implementación → evidencia

| Requisito | Implementación | Evidencia actual | Pendiente en Learner Lab |
| --- | --- | --- | --- |
| EC2 fuera de K8s, SG 8200 | `terraform/aws/vault` | Terraform validate satisfactorio | IDs, plan/apply y reglas SG |
| Inventario y topología | `docs/vault-lab.md` | Diagrama y nueve claves documentados | Identificadores reales sin datos privados |
| Ansible modular | `ansible/site.yml` y cinco roles | Syntax-check y lint Linux satisfactorios | Recap sobre EC2 |
| Instalación repetible | Binario/versiones/checksums fijados; handlers | Lint y parser de recap probados | Segundo recap con changed=0 |
| Destrucción/reconstrucción | `scripts/vault/rebuild-lab.sh` | Sintaxis Bash validada y límites de estado definidos | Ejecución aislada y reinstalación |
| Vault UI y TLS | Roles TLS/configuración/systemd | Vault real temporal probado con HTTPS | Captura de UI operativa y confianza del navegador |
| Init, unseal y persistencia | `scripts/vault/bootstrap.py`; Raft | Pruebas reales de sellado/unseal y recuperación | Ceremonia PGP y reinicio EC2 |
| KV y ACL de solo lectura | Políticas y rol CI con TTL máximo 4h | Lectura real permitida, escritura/administración denegadas | Accessor/TTL anonimizados y lectura del pipeline |
| GitHub → Vault | CD, SSM supervisor y vault-action | Workflow validado estáticamente | Enlace a ejecución autenticada mediante SSM |
| Secret → Helm → pods | Entrega transaccional y rolloutRevision | Render Helm y recuperación mediante pruebas controladas | Migración y smoke en EKS |
| Rotación | CAS en KV y renovación de cuatro consumidores | Generación/validación y transacción probadas | Rotación API y mismo SHA desplegado |
| Resiliencia | Suite real Vault + pruebas de entrega | Expiración, revocación, deny, sealed y rollback probados localmente | Ensayos contra el pipeline aislado |
| Auditoría | File audit con HMAC y logrotate/SIGHUP | Auditoría real sin contraseñas/tokens en claro | Registro de lectura de Actions anonimizado |
| Portafolio final | Esta matriz y guía de operación | Documentación versionada | Capturas, recaps y enlaces revisados |

## Validaciones reproducibles

Resultado ejecutado: **29 de 29 pruebas satisfactorias en Linux, sin omisiones**.
Incluyen entrega/recuperación controladas y Vault real con TLS, UI, KV, ACL,
expiración, revocación, sellado/unseal y persistencia Raft tras reiniciar.
La prueba POSIX verifica custodia 0600, directorio privado y creación exclusiva.
La prueba añadida del colector verifica salud real por HTTPS sin publicar tokens.
También pasaron Helm, Ansible, actionlint, Bash y los cinco stacks Terraform
incluidos en la matriz CI.

- Suite Python: pruebas de entrega/recuperación más pruebas contra un proceso
  Vault 2.2.0 TLS/Raft temporal. Ejecutar con `VAULT_TEST_BINARY` para que los
  casos reales no se omitan.
- Helm 3.17.3: lint y render del chart base, PostgreSQL y lab; comprobar
  namespace, frontend privado, anotaciones de cuatro consumidores y nuevo Job
  al repetir un SHA con otro intento.
- Terraform 1.12.2: `init -backend=false` y `validate` de los cinco stacks
  (`terraform`, AWS datos, bootstrap, EKS y Vault), sin acceso a una cuenta.
  `fmt -check` del stack Vault satisfactorio; AWS 5.100.0 fijado en su lockfile.
- Ansible: Python 3.12 en contenedor Linux, colecciones fijadas; syntax-check y
  ansible-lint sobre playbook y archivos de todos los roles.
- GitHub Actions: actionlint sobre los cinco workflows modificados/nuevos.
- Bash: `bash -n` para instalación SSM y reconstrucción; `git diff --check`.

Las pruebas de recuperación de Kubernetes usan un ejecutor controlado; no
acreditan un rollback real de EKS. La suite funcional existente será ejecutada
por CD dentro de catalogservice y crea únicamente datos sintéticos en el lab.
Las comprobaciones de idempotencia de Ansible sobre EC2, SSM y destrucción no
se sustituyen por lint o pruebas del parser.

## Panel de operaciones incorporado

Las vistas autenticadas `/admin/vault` y `/admin/ansible` se verificaron en el
panel local con navegador de escritorio y móvil. Pasaron las 20 pruebas SQL
(incluyendo seis contratos de operaciones), 31 comprobaciones HTTP de la
integración, `go test ./...` del frontend y cinco pruebas del callback/colector.
Se ejecutó `local-check.yml` realmente sobre localhost: tres tareas OK, cero
cambios y cero fallos. Su registro aparece en el panel y omite el resultado
protegido. Esto acredita la consola; no equivale a ejecutar `site.yml` en AWS.
Vault conserva el estado «Sin datos» hasta conectar el colector al servidor.
Operación y configuración: [guía del panel](admin-operations.md).

El registro ampliado añade módulo, rol, tiempos por tarea, códigos de salida,
reintentos y diagnósticos orientativos sin salida cruda. Verificación posterior:
22 pruebas SQL, 10 callback/colector, pruebas Go, lint/syntax Ansible y navegador
de escritorio/móvil. Se publicó otra ejecución local real: cuatro tareas OK,
cero cambios y cero fallos; la tarea de comando registra más de un segundo y
código de salida cero. El historial anterior se conserva. Las pruebas de interfaz
para fallos usaron una respuesta temporal interceptada, sin publicar datos ficticios.

## Evidencias a incorporar después de la ejecución remota

| Evidencia | Contenido permitido | Estado |
| --- | --- | --- |
| Infraestructura | ID EC2, separación de EKS, SG 8200 y cifrado | Pendiente |
| UI | Pantalla inicializada/unsealed y navegación KV sin mostrar valores | Pendiente |
| Ansible | Recaps primero/segundo y reconstrucción, sin diff privado | Pendiente |
| Pipeline exitoso | URL, SHA, ejecución/intento y resumen smoke | Pendiente |
| Auditoría | Ruta, timestamp y accessor/HMAC anonimizados; nunca token | Pendiente |
| Fallos y recuperación | Estado previo/posterior de release y resourceVersion | Pendiente |
| Persistencia | Reinicio Vault/PostgreSQL, quorum y datos preservados | Pendiente |

No versionar capturas de valores KV, tokens, claves privadas, participaciones
de unseal, kubeconfig, `.env`, archivos de custodia ni snapshots. Revisar cada
artefacto antes de moverlo desde `.vault-lab/evidence` al portafolio público.

La rúbrica visible nombra tres criterios de 25 puntos y anuncia cuatro. Se
conserva esa discrepancia; no se inventa el criterio faltante ni una nota total.
