## Panel de Administrador — Apparel Admin (Boutique-Eq4-DevOPS)

> Este proyecto parte del demo **Online Boutique** (11 microservicios, ver sección *Architecture* abajo). El trabajo de este equipo consiste en **agregar un feature completo sobre ese demo: el Panel de Administrador (back-office)**, además de definir la arquitectura de despliegue y el flujo de trabajo en equipo.

### Integrantes

- Norberto Suaste
- David Gzz

### Feature agregado: Panel de Administrador

Back-office para la tienda, diseñado primero en Figma (especificación visual completa) y construido en 4 módulos:

| Módulo | Qué hace |
|---|---|
| **Dashboard de Ventas** | KPIs de ingresos, órdenes, clientes nuevos y descuentos activos; gráfica de ventas del mes; ranking de productos más vendidos; actividad reciente de órdenes. |
| **Gestión de Catálogo** | Listado de productos con imagen, categoría, precio y estatus; filtros por Hombre/Mujer/Accesorios; alta, edición y eliminación; paginación para catálogos grandes. |
| **Control de Inventario** | Total de SKUs, stock crítico y embarques entrantes; filtros por disponibilidad; detalle por variante (talla/color) y almacén; alertas de stock bajo/agotado. |
| **Gestión de Pedidos** | Historial de órdenes con cliente, fecha, monto y método de pago; estatus (entregado, enviado, pendiente, cancelado); rango de fechas y exportación de reportes; detalle por pedido. |

Estado actual: diseño completo en Figma para las 4 secciones; construcción del frontend y conexión a backend en progreso.

### Arquitectura de despliegue

- **Registro de contenedores:** Amazon ECR, un repositorio por servicio bajo un prefijo compartido (`<account>.dkr.ecr.<region>.amazonaws.com/boutique/<service>`).
- **Cómputo:** Amazon ECS con tipo de lanzamiento EC2 (Auto Scaling Group de instancias `t3.medium` como proveedor de capacidad).
- **Redes:** `networkMode: bridge`; un Application Load Balancer al frente del servicio `frontend` (puerto 8080) enruta el tráfico a las instancias EC2.
- **Descubrimiento de servicios:** los servicios de backend se referencian por nombre (ej. `productcatalogservice:3550`) vía AWS Cloud Map (ECS Service Discovery) o un ALB compartido con ruteo basado en rutas.
- **Redis:** desplegado como tarea de ECS con la imagen pública `redis:alpine`.

### Planeación del proyecto

Se usó **GitHub Projects** con jerarquía Épica → Historia de Usuario → Tarea:

- **Épica:** Panel de Administrador.
- **Historias de Usuario:** una por módulo (Dashboard de ventas, Gestión de catálogo de productos, Control de inventario, Gestión de pedidos), gestionadas como *tracked issues*.
- **Tareas técnicas:** sub-issues numeradas (`FN-01`, `FN-02`, …) de diseño, frontend y conexión a backend, cada una con su propio estatus (Ready → In progress → In review → Done).
- Vistas usadas: **Backlog** (Kanban), **Team items** (jerarquía historia → tarea), **Roadmap** y **Priority board**.

### Estrategia de ramificación

- Cada desarrollador trabaja en su propia rama (`feature/…`) partiendo de `dev`.
- Los cambios se integran a `dev` mediante **Pull Request**, revisado y **aprobado por ambos desarrolladores** antes del merge.
- `dev` concentra el trabajo ya integrado y validado antes de pasar a producción.
- `main` permanece siempre estable: solo recibe merges desde `dev` y dispara el pipeline de **CI/CD (GitHub Actions)**, que construye la imagen y despliega directo al servicio correspondiente en ECS.

```
feature/david   ─┐                      ┌─ merge a main (CI/CD → ECS)
                 ├─ PR + revisión ──►dev─┤
feature/norberto─┘   cruzada             
```

