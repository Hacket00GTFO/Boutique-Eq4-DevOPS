# Panel de Administración (Apparel Admin)

Panel back-office servido por el servicio Go existente `src/frontend` bajo
las rutas `/admin/*`. **Fase actual: solo frontend** — todos los datos son
mock y la autenticación es una cuenta hardcodeada. Este documento describe la
estructura y el contrato para la futura integración del backend.

## Acceso (demo)

| | |
|---|---|
| URL | `http://<frontend>/admin` (redirige a `/admin/login` sin sesión) |
| Email | `admin@boutique.com` |
| Password | `admin123` |
| Override | env vars `ADMIN_EMAIL` / `ADMIN_PASSWORD` |

## Mapa de rutas

| Ruta | Método | Auth | Descripción |
|---|---|---|---|
| `/admin/login` | GET | pública | Formulario de login |
| `/admin/login` | POST | pública | Valida credenciales, crea sesión |
| `/admin` | GET | protegida | Dashboard (stats, ventas del mes, top productos, actividad reciente) |
| `/admin/pedidos` | GET | protegida | Tabla de órdenes (`?page=`, `?range=`) |
| `/admin/catalogo` | GET | protegida | Productos (`?category=all|men|women|accessories`, `?page=`) |
| `/admin/inventario` | GET | protegida | Stock (`?stock=all|in|low|out`, `?page=`) |
| `/admin/logout` | GET | protegida | Destruye la sesión |

Las rutas protegidas usan el middleware `requireAdminAuth` sobre un subrouter
de mux (`src/frontend/main.go`). Sin cookie válida → `303` a `/admin/login`.

## Autenticación (`admin_auth.go`)

- Cookie `admin_session`: UUID v4 en `HttpOnly`, `SameSite=Lax`, TTL 24h.
- Store: `map[string]time.Time` en memoria con `sync.Mutex` (se pierde al
  reiniciar el pod — aceptable en fase frontend-only).
- `checkAdminCredentials(email, password)`: comparación directa contra
  `ADMIN_EMAIL`/`ADMIN_PASSWORD` (defaults hardcodeados).

**Para producción:** reemplazar por un servicio de auth real (JWT u OIDC),
passwords con hash (bcrypt/argon2), cookie `Secure`, CSRF token en el form de
login, y rate limiting de intentos.

## Capa de datos (`admin_data.go`)

Los handlers no conocen el origen de datos: consumen la interfaz
`AdminDataProvider`. Hoy la implementa `mockAdminData` (datos hardcodeados
replicando los mockups). Para el backend real, implementar la interfaz con
clientes gRPC/REST y cambiar una línea en `admin_handlers.go`:

```go
var adminData AdminDataProvider = mockAdminData{} // hoy
// var adminData AdminDataProvider = grpcAdminData{...} // futuro
```

### Contrato `AdminDataProvider` → backend futuro

| Método | Datos | Fuente sugerida |
|---|---|---|
| `Dashboard()` | stats de revenue/órdenes/clientes/descuentos, ventas mensuales, top productos, actividad reciente | `productcatalogservice` (productos) + servicio de órdenes/analytics **a definir** |
| `Orders(page, dateRange)` | órdenes paginadas con status y método de pago | servicio de órdenes **a definir** (hoy `checkoutservice` no persiste órdenes) |
| `Products(page, category)` | catálogo paginado con status | `productcatalogservice` (`ListProducts`) + campo `status` nuevo en el proto |
| `Inventory(page, stockFilter)` | SKUs, variantes, stock por warehouse | servicio de inventario **a definir** |

### Stubs pendientes de backend

- **Export / Exportar** (dashboard, pedidos, catálogo, inventario): sin endpoint; el botón es visual.
- **Añadir Producto / Añadir Stock**: formularios y endpoints POST pendientes.
- **Ver Detalles** (pedidos): falta `/admin/pedidos/{id}` + servicio de órdenes.
- **Acciones edit/delete** (catálogo, inventario): endpoints de mutación pendientes.
- **Paginación**: números/flechas son visuales; el mock devuelve siempre la primera página.
- **Ajustes** (sidebar): página no implementada.
- **Búsqueda** (topbar): input visual, sin endpoint.
- **Rango de fechas / Warehouse filter**: controles visuales.

## Estructura de archivos

```
src/frontend/
  admin_auth.go        # credenciales, session store, requireAdminAuth
  admin_data.go        # AdminDataProvider + mockAdminData (datos de mockup)
  admin_handlers.go    # handlers + adminTemplates (ParseGlob templates/admin)
  templates/admin/
    layout.html        # parciales: admin_head, admin_sidebar, admin_topbar
    login.html         # define "admin_login" (standalone)
    dashboard.html     # define "admin_dashboard"
    orders.html        # define "admin_orders"
    catalog.html       # define "admin_catalog"
    inventory.html     # define "admin_inventory"
  static/admin/
    admin.css          # estilos del panel (sin Bootstrap)
    admin.js           # init Chart.js desde atributos data-values
```

Notas:

- Los templates admin se parsean con un glob propio
  (`templates/admin/*.html`); el glob existente `templates/*.html` no recurse,
  así que no hay colisión de nombres `define` con la tienda.
- Gráficas: Chart.js 4 por CDN; los datos viajan en `data-values` (CSV) que
  `admin.js` lee — evita inyectar JS desde templates.
- Iconos: Google Symbols (misma CDN que la tienda).
- Imágenes de producto: placeholders de `static/img/products/`; el backend
  real debe devolver `picture` por producto.
- Dockerfile: sin cambios (`COPY ./templates` y `./static` cubren los subdirs).

## Desarrollo local

El panel no llama a ningún gRPC, así que el frontend arranca con addrs dummy:

```powershell
cd src/frontend
$env:PRODUCT_CATALOG_SERVICE_ADDR="localhost:1"
$env:CURRENCY_SERVICE_ADDR="localhost:1"
$env:CART_SERVICE_ADDR="localhost:1"
$env:RECOMMENDATION_SERVICE_ADDR="localhost:1"
$env:CHECKOUT_SERVICE_ADDR="localhost:1"
$env:SHIPPING_SERVICE_ADDR="localhost:1"
$env:AD_SERVICE_ADDR="localhost:1"
$env:SHOPPING_ASSISTANT_SERVICE_ADDR="localhost:1"
go run .
# http://localhost:8080/admin/login
```
