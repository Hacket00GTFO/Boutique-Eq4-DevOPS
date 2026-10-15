# Demo administrativo con PostgreSQL

El modo demo usa registros reales en la base local y conserva los cambios entre
reinicios. Los pagos y envíos son simulados; guardar una guía no solicita un
servicio a una paquetería externa.

## Acceso

```powershell
.\scripts\start-integration.ps1
```

Abrir http://localhost:8080/admin/login y usar `ADMIN_EMAIL` / `ADMIN_PASSWORD`
del archivo local `.env`. El arranque aplica la migración `002_demo_shipments.sql`
sin eliminar productos ni pedidos anteriores.

Con las credenciales ya configuradas, `docker compose up -d --build` también
activa este modo mediante `docker-compose.override.yml`. Para revisar la
configuración y comprobar que las plantillas y recursos del contenedor coinciden
con el código local, ejecutar `.\scripts\check-integration.ps1 -VerifyFrontendFiles`.

## Cargar ejemplos

El botón **Cargar datos demo** está en Dashboard, Catálogo, Inventario y Pedidos.
Agrega cuatro SKU: camiseta, reloj, lentes y zapatos; incluye stock disponible,
bajo y agotado. Crea tres pedidos identificados como demo: pendiente de envío,
enviado y entregado, con clientes `@example.invalid` y dirección ficticia.

Se puede volver a pulsar: no duplica registros ni restablece precios, stock,
archivos o envíos ya modificados. Los ejemplos conviven con el catálogo original.
Buscar `demo` en Catálogo o filtrar el almacén **Demo Central** en Inventario.

## Gestión desde el frontend

| Pantalla | Acciones |
| --- | --- |
| Catálogo | Añadir, editar, archivar, activar, filtrar por categoría/estado y exportar CSV. El botón Inventario abre el SKU y sus movimientos. |
| Inventario | Ver existencias totales, disponibles y reservadas. Movimientos / recepción permite registrar una entrada o salida con motivo y consultar los últimos 100 movimientos. |
| Pedidos | Crear un pedido demo con cliente, dirección, varios SKU, cantidades y costo de envío USD. Consultar, filtrar por cliente/estado/fecha, exportar y abrir Detalles y envío. |
| Detalle | Consultar productos, cantidades, precios históricos, subtotales, total, cliente, dirección y referencia de pago demo. |
| Envío dentro del detalle | Guardar paquetería, guía, fecha estimada y notas; enviar, registrar incidencias y marcar entrega. Consultar los últimos 100 eventos con fecha UTC y responsable. |

El pedido demo se confirma con pago simulado, descuenta existencias y queda
**PENDIENTE** de envío. Reenviar el mismo formulario conserva el mismo pedido.
Si se intenta reutilizar su llave con datos distintos, se informa un conflicto.
Un producto archivado o sin existencias no se puede vender. Los precios se
calculan en el servidor a partir de PostgreSQL.

El envío avanza **PENDIENTE → ENVIADO → ENTREGADO**. Desde ENVIADO se puede pasar
a **INCIDENCIA**, dejar su motivo y luego resolverla a ENVIADO o ENTREGADO.
Para enviar se requieren paquetería y guía. Una entrega finalizada no vuelve a
estados anteriores. Las ediciones concurrentes se rechazan con un mensaje para
recargar el detalle.

**Cancelar pedido y devolver stock** está disponible únicamente para pedidos
demo confirmados que todavía estén pendientes de envío. Solicita un motivo,
devuelve las unidades y registra movimientos una sola vez. Pedidos enviados
y compras del checkout no admiten esa cancelación demo.

Los botones de archivo, cancelación y entrega piden confirmación en el navegador.
Las operaciones requieren sesión, CSRF y rol administrador; los permisos se
verifican también en la API.

## Configuración y pruebas

Compose habilita `DEMO_DATA_ENABLED=true` en frontend y backend. Fuera de ese
entorno las operaciones demo están deshabilitadas por defecto. En un laboratorio
Helm se pueden activar con `postgresIntegration.demoDataEnabled=true` en ambos
servicios; esa opción no carga ejemplos automáticamente.

```powershell
.\scripts\test-integration.ps1
.\scripts\test-storefront-integration.ps1
.\scripts\test-persistence.ps1
```

La suite SQL usa `boutique_test`. La suite web crea SKU propios y pedidos
sintéticos en la tienda local, archiva esos SKU al terminar y deja pedidos como
histórico de prueba. También carga los ejemplos mediante el botón del panel.
No ejecutar estas pruebas contra datos de producción.

Resultados: [verificación del 14 de octubre de 2026](demo-verification-2026-10-14.md).
