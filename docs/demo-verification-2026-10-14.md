# Verificación del demo administrativo persistente

Fecha: 14 de octubre de 2026, America/Mexico_City.

## Resultado

El frontend local está conectado a PostgreSQL y tiene operaciones demo para
catálogo, inventario, pedidos y envíos. Se aplicó la migración incremental
`002_demo_shipments.sql` sin eliminar volúmenes ni registros anteriores.

| Validación ejecutada | Evidencia |
| --- | --- |
| Construcción Docker frontend y backend | Satisfactoria |
| PostgreSQL aislado, `boutique_test` | 14 tests satisfactorios |
| Formularios frontend → backend → PostgreSQL | 30 comprobaciones satisfactorias |
| Reinicio PostgreSQL, backend y frontend | Sesión, pedido, guía, eventos de envío e inventario conservados |
| Go frontend | `go test ./...` satisfactorio; imagen final compilada |
| Helm con modo demo activado | Lint satisfactorio; frontend y backend reciben `DEMO_DATA_ENABLED="true"` |
| Navegador local | Login, panel, lista, detalle y formulario con varias líneas verificados |
| Diff | `git diff --check` satisfactorio |

Las pruebas verifican carga repetida sin duplicados ni restablecimiento de
stock, reactivación de productos, entradas/salidas e historial, precios y
totales exactos, pedidos con varios SKU y reintentos sin doble consumo. También
cubren cancelación demo con devolución única, permisos/CSRF, cantidades inválidas,
última unidad bajo concurrencia, versiones de envío, guía/paquetería obligatorias,
incidencias, entrega e historial de eventos persistente.

La revisión de navegador confirmó el botón **Añadir otro producto** y los
controles de envío dentro del mismo detalle de pedido. El script de persistencia
comparó datos y eventos antes/después del reinicio con una sesión real.

## Alcance

La carga local dejó cuatro productos y tres pedidos de ejemplo, además de los
pedidos sintéticos de la suite HTTP. Los SKU E2E se archivan al terminar; sus
pedidos permanecen como histórico local. No se hicieron pagos, envíos ni
despliegues AWS reales. Helm mantiene las operaciones demo deshabilitadas por
defecto y permite activarlas explícitamente para un laboratorio.

Guía de uso: [demo-management.md](demo-management.md).
