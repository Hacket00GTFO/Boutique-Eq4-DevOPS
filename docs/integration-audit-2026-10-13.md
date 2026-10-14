# Evidencia de auditoría local de integración

Fecha local: 13 de octubre de 2026, America/Mexico_City. Checkout analizado: `45ce2808`.

## Ejecución registrada

La ejecución final de `scripts/audit-local-integration.py` finalizó el **13 de octubre a las 19:30:59** de Ciudad de México (`2026-10-14T01:30:59.566337+00:00`). Salida: **31 pass, 0 fail**, exit code 0.

No se construyeron imágenes ni se reiniciaron servicios. El script se ejecutó desde el Python de `recommendationservice`, dentro de la red local existente, con cookies de una sesión nueva. No se comprobó equivalencia exacta entre imágenes en ejecución y el commit del checkout.

| Verificación | Resultado |
| --- | --- |
| Configuración Compose | `docker compose config --quiet`, sin errores |
| Contenedores base | 11 running; 6 healthy, 5 sin healthcheck Docker |
| Red | `boutique-eq4-devops_default`; servicios resueltos por nombre |
| gRPC health | 9/9 SERVING: catálogo, moneda, carrito, recomendaciones, envío, checkout, anuncios, pago y correo |
| ListProducts | 9 productos |
| Recomendaciones | 5 IDs válidos, excluyendo el producto consultado |
| Anuncios | 2 anuncios |
| Conversión de moneda | USD → EUR, importe positivo |
| Cotización de envío | Moneda USD |
| Health HTTP | `/_healthz` 200 |
| Tienda | Home y detalle de producto 200 |
| Protección admin | `/admin` sin sesión termina en `/admin/login` |
| Login admin demo | Termina en `/admin` |
| Vistas admin | Dashboard, catálogo, inventario y pedidos 200 con sesión válida |
| Cambio de moneda HTTP | Cookie `shop_currency=EUR` |
| Agregar carrito HTTP | Consulta gRPC posterior comprueba mismo producto, cantidad 1 |
| Carrito HTTP | 200 |
| Checkout simulado | 200 y texto de confirmación con UUID `e9044ff6-c76e-11f1-8804-06dae53fe2b5` |
| Carrito tras checkout | 0 ítems por consulta gRPC |
| Limpieza | Carrito de prueba vacío, sesión admin cerrada |

Los 31 checks incluyen los 9 probes y las operaciones/limpiezas individuales; la tabla agrupa algunos para facilitar lectura.

En esa ejecución: home 59 ms, producto 26 ms, carrito 20 ms y checkout 34 ms. En una ejecución preliminar de 27 checks todos pasaron, pero checkout tardó 5847 ms y producto 1143 ms. Son observaciones puntuales con redirecciones incluidas, sin carga controlada, que **no establecen rendimiento garantizado ni explican por sí solas la causa de la variación**.

## Estado de almacenamiento y despliegue

- Compose no incluye `catalogservice` ni PostgreSQL.
- Redis local: `appendonly=no`, `save=3600 1 300 100 60 10000`; sin volumen montado. No se probó recuperación ni pérdida de datos.
- Admin usa mocks y sesiones en memoria, según código. Que sus vistas respondan 200 no acredita CRUD o persistencia.
- Catálogo de la tienda usa archivo JSON por defecto; backend administrativo propuesto en el repo usa DynamoDB para contar productos.
- Checkout devuelve una orden, pero el código no la persiste. Payment/email/shipping son simulados.
- Las 13 task definitions ECS usan `bridge` y no incluyen `logConfiguration` ni `healthCheck`; hay puertos de host repetidos. Son archivos inspeccionados, no tareas AWS consultadas.
- Shopping assistant y loadgenerator son perfiles opcionales no activos en esta auditoría.
- No se verificó un PostgreSQL externo, una cuenta AWS, despliegue remoto, backups, carga o escenarios de fallo.

Plan derivado: [backend-integration-plan.md](backend-integration-plan.md).
