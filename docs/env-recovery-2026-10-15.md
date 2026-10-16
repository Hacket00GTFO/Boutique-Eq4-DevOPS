# Recuperación de la configuración local

Fecha: 15 de octubre de 2026, America/Mexico_City.

Compose se detenía durante la interpolación porque `.env` no existía. No era
un fallo de compilación. Los contenedores de este mismo workspace conservaban
las siete variables necesarias, incluido el migrador finalizado.

Se restauró `.env` con esos valores sin imprimir secretos, modificar contraseñas
ni borrar volúmenes. Se completó `docker-compose up -d --build --wait` y se
validó el acceso con la cuenta recuperada y la lectura de productos, inventario
y pedidos. `.env` sigue excluido de Git.

`setup-integration.ps1` ahora conserva archivos completos, repara claves ausentes
o vacías desde los contenedores del workspace y comprueba si hay PostgreSQL
existente antes de generar credenciales. Si solo queda el volumen y faltan
claves irrecuperables, se detiene sin modificar el archivo. `start-integration.ps1`
ejecuta esta preparación en cada arranque.

Se ejecutaron seis pruebas con metadatos Docker ficticios en directorios
temporales, en PowerShell 7 y Windows PowerShell 5.1: instalación nueva,
preservación de un archivo completo, recuperación de archivo ausente, reparación
parcial, bloqueo de regeneración con volumen existente y fallo de inspección de
Docker. Se añadieron al workflow de integración.

La comprobación de imágenes distingue ahora el índice OCI de su manifiesto
ejecutable: reconstruir una imagen idéntica puede cambiar únicamente sus
atestaciones. Se verificó reconstruyendo de nuevo frontend; el diagnóstico
validó correctamente su manifiesto, configuración y hashes de plantillas/CSS/JS.

Arranque recomendado:

```powershell
.\scripts\start-integration.ps1
```

Solo preparar la configuración, sin levantar servicios:

```powershell
.\scripts\setup-integration.ps1
docker-compose up -d --build
```
