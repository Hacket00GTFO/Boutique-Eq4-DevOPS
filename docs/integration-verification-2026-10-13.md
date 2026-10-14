# Verificación de la integración PostgreSQL

Fecha de trabajo: 13 de octubre de 2026, America/Mexico_City.
Los cambios permanecen en el workspace; no se desplegó una cuenta AWS.

## Resultados

| Validación | Resultado |
| --- | --- |
| Construcción Docker | Backend, frontend, catálogo, checkout, recomendaciones, pago y envío compilados |
| Configuración Compose base + override PostgreSQL | Válida |
| PostgreSQL real, base boutique_test | 9 tests satisfactorios |
| HTTP frontend → gRPC → backend → PostgreSQL | 17 comprobaciones satisfactorias |
| Reinicio PostgreSQL/backend/frontend | Pedido y sesión administrativa conservados |
| Worker de correo | Dos pedidos sintéticos confirmados con email_sent=true; servicio de correo simulado |
| Go frontend | go test ./... satisfactorio; paquetes money y validator |
| Go checkout | go test ./... satisfactorio; paquete money y go vet del servicio |
| Helm base e integración | Lint satisfactorio |
| Helm con PostgreSQL, políticas de red y Sidecars | Render YAML satisfactorio |
| Puerto publicado en Windows | http://localhost:8080/_readyz → 200 ready |
| Credenciales | .env excluido de Git |
| Diff | git diff --check satisfactorio |

La suite PostgreSQL cubre permisos/sesiones, catálogo con varias categorías,
edición concurrente, última unidad bajo concurrencia, idempotencia, consumo
único de stock, liberación de reservas, conciliación manual, queries del
dashboard, filtros/paginación y CSV.

La suite HTTP crea y edita un SKU desde el panel, lo consulta en la tienda,
ajusta stock, compra en EUR, repite el mismo intento sin duplicar compra/stock,
rechaza reutilización de llave con otros datos, consulta el detalle, marca
entrega y exporta. También verifica login, credenciales incorrectas y CSRF.
Los SKU sintéticos se archivan al terminar; sus pedidos quedan en el histórico.

Se detectaron y corrigieron durante la validación una consulta SQL que usaba
un alias reservado, la normalización de correo en una fixture, el manejo de
errores de login, el formato de errores que rechazaba go vet y la edición de
categorías múltiples del catálogo existente.

## Alcance de la evidencia

Las pruebas usan los simuladores originales de pago, envío y correo. No se
hicieron pagos ni envíos reales. Las pruebas de datos se ejecutan en una base
aislada; las pruebas HTTP dejan pedidos sintéticos únicamente en la tienda
local. No se eliminaron volúmenes de la integración al terminar.

El render de Helm acredita sintaxis/configuración, no un despliegue remoto,
TLS de un proveedor, capacidad de nodos ni permisos IAM. No hay evidencia de
backup/restore, rendimiento con carga o comportamiento de proveedores reales.

Operación, contratos y límites: [postgres-integration.md](postgres-integration.md).
