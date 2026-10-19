# Operación de Vault y consola de Ansible

El panel incorpora `/admin/vault` y `/admin/ansible`. Ambas requieren sesión;
en modo integrado, el backend exige además rol `admin`. Los usuarios `viewer`
no pueden consultar estos reportes. Los datos se conservan en PostgreSQL mediante
la migración `003_operations.sql`; el frontend utiliza la API privada existente.

## Qué muestra cada vista

- **Vault:** última salud recibida por HTTPS, inicialización, sellado, standby,
  versión, fecha de lectura y antigüedad. Una lectura de más de 60 segundos se
  marca antigua. No se confunde falta de información con un servidor operativo.
- **Ansible:** últimas 30 ejecuciones, resultado, duración, contadores y consola
  de hasta 400 eventos por ejecución. Incluye búsqueda, filtro de errores,
  pausa, seguimiento de nuevas líneas y descarga del registro estructurado.
- Ambas se actualizan cada cinco segundos mientras la pestaña es visible.
  La consola señala ejecuciones sin actualizaciones durante más de 120 segundos;
  una tarea larga puede seguir ejecutándose, por lo que no se marca fallida.

La consola muestra eventos reales de Ansible, no una shell interactiva. Cada
tarea incluye módulo, rol cuando existe, destino, fecha, duración en milisegundos,
código de salida cuando el módulo lo proporciona e intentos de reejecución.
Los bucles muestran cantidades de elementos y fallos, sin sus valores. Los
contadores avanzan durante la ejecución y el recap final conserva el resultado
oficial de Ansible, incluidos los errores recuperados por el playbook.

Los fallos muestran un **diagnóstico orientativo** y una comprobación sugerida:
permisos, autenticación, certificado TLS, checksum, falta de espacio, conexión,
timeout, dependencia ausente, sintaxis o validación. La clasificación utiliza
patrones en el controlador, pero no envía los mensajes originales. Si no reconoce
la causa, indica consultar el registro privado del controlador. No equivale a
una explicación completa del fallo ni sustituye ese registro.

El callback excluye argumentos, stdout/stderr, excepciones, diferencias de archivos
y resultados `no_log`. Una tarea protegida conserva solamente estado, identificador
y tiempos, además del destino; se omiten su nombre, módulo, rol y diagnóstico.
El backend valida de nuevo los campos y genera el texto de los diagnósticos desde
un catálogo fijo. Nunca acepta mensajes de error arbitrarios del productor.
Los registros antiguos siguen disponibles con los campos que recogieron; el
detalle nuevo aparece en las nuevas ejecuciones. Búsqueda, filtro de errores
(incluye reintentos) y descarga incorporan los detalles nuevos. Cada reporte
retiene hasta 400 eventos y se recorta por bytes antes de superar el límite de envío.
No se entregan tokens de Vault, participaciones ni claves privadas al navegador.

## Conectar reportes reales

Las vistas funcionan sin agentes configurados y muestran estados vacíos.
No se generan ejecuciones ficticias. El servidor Vault de AWS sigue requiriendo
el aprovisionamiento y bootstrap descritos en [la guía Vault](vault-lab.md).

El colector necesita acceso al backend privado y su credencial de servicio
`ADMIN_API_TOKEN`. Suministrarla mediante una sesión privada o gestor de
credenciales como `OPS_REPORT_TOKEN`; no escribirla en la línea de comandos.
La API de reportes no tiene proxy POST en el frontend y no permite ejecutar comandos.

En Kubernetes, usar un port-forward privado en una terminal:

```bash
kubectl -n boutique-vault-lab port-forward service/catalogservice 18080:80
```

En otra terminal del operador:

```bash
export OPS_REPORT_URL=http://127.0.0.1:18080
read -rsp 'Credencial privada de servicio: ' OPS_REPORT_TOKEN; echo
export OPS_REPORT_TOKEN
# VAULT_ADDR y VAULT_CACERT apuntan al túnel SSM con TLS verificado.
python scripts/operations_report.py vault --watch
```

El colector consulta solamente `/v1/sys/health`, sin token de Vault. Publica
también la imposibilidad de verificar la conexión; no omite silenciosamente
una caída. Las conexiones de reporte públicas requieren HTTPS verificado y
no siguen redirecciones. `OPS_REPORT_CA` permite una CA privada del backend.

Para Ansible, ejecutar desde `ansible/` con las mismas variables `OPS_REPORT_*`:

```bash
cd ansible
ansible-playbook site.yml
```

`boutique_ops` está habilitado en `ansible.cfg`. Escribe reportes estructurados
en `.vault-lab/runs/<uuid>.json` y los publica durante la ejecución si se
configuró `OPS_REPORT_URL`. Si no hay conexión, la ejecución de Ansible continúa
y el archivo puede publicarse después:

```bash
python scripts/operations_report.py publish .vault-lab/runs/ID-DE-EJECUCION.json
```

Para comprobar el registro sin intervenir AWS:

```bash
cd ansible
ansible-playbook -i localhost, local-check.yml
```

Esta ejecución se identifica como `local-check.yml`; acredita el colector y la
consola, no una instalación de Vault. No modifica infraestructura.

## Abrir la UI nativa de Vault

Opcionalmente configurar `VAULT_PUBLIC_UI_URL` en `.env` para Compose, o
`operations.vaultUIURL` en Helm, con una URL HTTPS accesible desde el navegador
del operador. Aparece el botón **Abrir Vault UI**; los certificados deben estar
en el almacén de confianza. La autenticación sigue ocurriendo en Vault.

## Contratos y límites

- `POST /api/v1/operations/reports`: autenticación de servicio, tipos `vault` o
  `ansible`, UUID, secuencia creciente y campos explícitos. Máximo 256 KiB,
  400 eventos y retención de 200 reportes por tipo. Se ignoran actualizaciones
  fuera de orden y cambios de una ejecución ya terminada.
- `GET /api/v1/operations/vault` y `/ansible?run_id=<uuid>`: sesión de administrador.
- `/admin/operaciones/vault.json` y `/ansible.json`: proxy autenticado de lectura,
  sin caché, utilizado por las vistas. La fecha de recepción la fija el backend.
- La salud no acredita políticas/KV ni el éxito de CI/CD. El contrato previsto
  aparece identificado como tal en la interfaz; su evidencia proviene del pipeline.

Validaciones: pruebas Go de autenticación/plantillas/escape, contratos PostgreSQL
con permisos, saneamiento y orden de eventos, pruebas del callback con `no_log`
y comprobación de la UI en navegador. Las pruebas SQL usan `boutique_test`.

Verificación inicial ejecutada: 20 pruebas SQL, 31 comprobaciones HTTP,
`go test ./...`, cinco pruebas callback/colector y 29 pruebas Vault. El navegador
comprobó login, navegación, filtros, pausa, descarga, ausencia de resultados
`no_log` y diseño sin desbordamientos a 1440 y 390 píxeles. Se publicó una
ejecución real de `local-check.yml`; ahora incluye una cuarta tarea de un segundo
para comprobar duración y código de salida (4 OK, 0 cambios, 0 fallos). No se simuló
una instalación AWS ni se publicó una lectura ficticia de Vault.

Ampliación del registro verificada con 22 pruebas SQL, 10 pruebas del callback y
colector, `go test ./...`, Ansible syntax-check/lint y una ejecución local real
con el callback habilitado explícitamente. El navegador comprobó tiempos, módulo,
código de salida cero, búsqueda por módulo/diagnóstico, descarga detallada,
protección de resultados y tamaños de escritorio/móvil. Los fallos clasificados
se probaron en contratos y mediante una respuesta interceptada solo en el navegador;
no se guardaron ejecuciones ficticias en el panel.
