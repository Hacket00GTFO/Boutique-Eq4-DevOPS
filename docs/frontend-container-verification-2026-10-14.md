# Verificación del contenedor frontend

Fecha: 14 de octubre de 2026, America/Mexico_City.

## Causa de las pantallas con acciones vacías

El contenedor usaba la imagen etiquetada actualmente como `frontend:local`,
pero su etiqueta `com.docker.compose.project.config_files` contenía únicamente
`docker-compose.yml`. No tenía `ADMIN_DATA_MODE=backend`, `ADMIN_API_BASE_URL`
ni `DEMO_DATA_ENABLED=true`. Catálogo y checkout tampoco tenían las direcciones
de sus API persistentes.

Por eso aparecían los registros fijos de `mockAdminData`, contadores como 1.248
SKU y pedidos de 2023. Las plantillas ocultan los botones de gestión cuando
el backend está deshabilitado. Construir la imagen no activa esas variables.

## Corrección

- `docker-compose.override.yml` activa automáticamente la integración al ejecutar
  `docker compose up` o `docker-compose up`, reutilizando las definiciones de
  `docker-compose.admin.yml` mediante `extends`.
- Se compararon los modelos JSON expandidos del arranque normal y del explícito:
  resultaron idénticos, con 14 servicios definidos, incluido el migrador.
- Se ejecutó `docker compose up -d --build --wait --wait-timeout 180` y se
  recrearon frontend, catálogo, checkout y Redis con las conexiones correctas.
  Los volúmenes PostgreSQL/Redis se conservaron; Redis del arranque mock tenía
  cero claves antes de su recreación.
- `start-integration.ps1` ahora comprueba también la configuración activa y
  las imágenes. La verificación se añadió al workflow de integración, cuyo
  arranque usa el comando normal para cubrir este caso.

## Evidencia

| Comprobación | Resultado |
| --- | --- |
| Diagnóstico antes de corregir | Detectó `ORDER_API_BASE_URL` ausente y rechazó el entorno |
| Configuración activa después | Frontend en backend/demo persistente; catálogo y checkout conectados a catalogservice |
| Imagen activa frente a su etiqueta | Coincide en los cuatro servicios de integración |
| Plantillas frontend del contenedor | Todas coinciden con el workspace mediante SHA256 |
| CSS y JavaScript administrativos | Todos coinciden con el workspace mediante SHA256 |
| Diagnóstico en PowerShell 7 y Windows PowerShell 5.1 | Satisfactorio |
| `/_readyz` con backend/PostgreSQL | 200 ready |
| Flujo web completo | 30 comprobaciones satisfactorias |
| Navegador local | Catálogo, inventario y pedidos muestran los botones de gestión y datos persistentes |

Se verificó visualmente Añadir producto, Editar, Archivar, Inventario,
Movimientos / recepción, Aplicar, Crear pedido demo y Detalles y envío.

## Comandos de operación

```powershell
docker compose up -d --build --wait
.\scripts\check-integration.ps1 -VerifyFrontendFiles
```

Para una instalación nueva usar primero `.\scripts\start-integration.ps1`,
que genera `.env` si falta. Tras pasar del mock al backend, iniciar sesión
con `ADMIN_EMAIL` y `ADMIN_PASSWORD` de `.env`. El comando explícito
`docker compose -f docker-compose.yml up` sigue reservado al mock original.
