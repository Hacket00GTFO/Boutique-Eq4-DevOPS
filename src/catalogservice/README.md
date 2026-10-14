# Backend administrativo PostgreSQL

API Flask/Gunicorn con módulos de catálogo, inventario, pedidos, autenticación
y reportes. Reemplaza el placeholder que contaba registros en DynamoDB.

El frontend, el catálogo gRPC y checkout la consumen mediante tokens internos
separados. Solo este backend escribe en PostgreSQL; las sesiones y los pedidos
persisten entre workers y reinicios. La migración se ejecuta en un job separado.

Ver [configuración, contratos, pruebas y despliegue](../../docs/postgres-integration.md).
Desde la raíz:

```powershell
./scripts/setup-integration.ps1
docker compose -f docker-compose.yml -f docker-compose.admin.yml up -d --build --wait
./scripts/test-integration.ps1
./scripts/test-storefront-integration.ps1
```

Las credenciales locales están en `.env` y no se incluyen en Git. El modo
backend requiere PostgreSQL y no hace fallback a datos simulados.
