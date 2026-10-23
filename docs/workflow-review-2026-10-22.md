# Revisión de workflows del 22 de octubre de 2026

Se revisaron los 14 workflows de `main` en el commit
`bb0123d31cf445322124f8497c0cf7e15eeb9c8b`, después del PR #54.
Las correcciones de esta revisión se entregan exclusivamente en `development`.
El responsable del repositorio crea y fusiona el PR hacia `main`.

## Hallazgos y correcciones

| Prioridad | Hallazgo en main | Corrección preparada en development |
| --- | --- | --- |
| Alta | Bootstrap y CD consultan Vault inmediatamente después de lanzar SSM en segundo plano. | Esperar señal atómica del supervisor y disponibilidad KMS/TLS, con plazos separados y cierre verificado. |
| Alta | Un dispatch manual `inventory` en main puede autorizar una release cuando la instalación esté activada. | Operación `release` explícita y autorización probada por evento, rama, repositorio y SHA. |
| Alta | Bootstrap y CD no comparten exclusión; la custodia solo se sube si todos los pasos anteriores pasan. | Bloqueo común y subida de custodia cifrada incluso ante fallo posterior. |
| Alta | Configurar la confianza OIDC puede activar CD sin demostrar un login real desde GitHub. | Activación desde un bootstrap exitoso, vinculado al intento, infraestructura, datos y copia permanente de custodia. |
| Alta | Las pruebas unitarias de main son independientes del build de ECR. | Workflow compartido Go/C# obligatorio junto con integración PostgreSQL y seguridad Vault antes de publicar las doce imágenes. |
| Alta | El bootstrap actual no recupera el arranque fallido de los trabajadores antes de esperar PostgreSQL. | Recuperación inicial acotada al estado confirmado, con permisos actuales, tres instancias conocidas, roles esperados, cluster vacío y namespace de aplicación ausente. |
| Media | `cancel-in-progress: false` conserva solo una ejecución pendiente por defecto. | `queue: max`, hasta 100 pendientes. |
| Media | CD exige EBS CSI ACTIVE antes de esperar la recuperación de sus nodos y no comprueba la imagen loadgenerator. | Primero nodos Ready, después EBS CSI; comprobar las doce imágenes. |
| Media | No se vincula la entrega posterior al PVC y volumen EBS realmente recuperados. | Comprobar identidad de PVC/PV/EBS, esquema y administrador antes de escribir. |
| Media | ECS puede desplegar además de EKS si se activa su variable heredada. | ECS pasa a dispatch manual explícito en main. |
| Media | Terraform de datos admite dispatch desde otras ramas, carece de exclusión y usa una versión distinta a la validada. | Guard de main, cola de datos, permisos de lectura, límite de tiempo y versión 1.12.2 alineada con la validación existente. |
| Media | Un push directo a development no ejecuta las tres verificaciones principales. | CI unitario, integración PostgreSQL y seguridad Vault en development, sin despliegue. |

Los workflows de Helm, Kustomize y validación Terraform también reciben permisos
explícitos de lectura y límites de tiempo. La sincronización hacia las ramas de
equipo conserva su comportamiento; un push directo a development no la dispara.

## Evidencia de ejecución

- [Release del PR #54](https://github.com/Hacket00GTFO/Boutique-Eq4-DevOPS/actions/runs/39829051808):
  `requirements` falla porque `DATA_RECOVERY_VERIFIED=false`. El control ocurre
  antes de configurar credenciales AWS. Integración y Vault aprobaron; no hubo
  publicación ni despliegue.
- [Bootstrap anterior](https://github.com/Hacket00GTFO/Boutique-Eq4-DevOPS/actions/runs/39825138941):
  AWS y SSM respondieron, pero no se registró ningún trabajador EKS antes del
  plazo de espera. No llegó al login OIDC ni a la restauración PostgreSQL.
- [Diagnóstico de lectura en development](https://github.com/Hacket00GTFO/Boutique-Eq4-DevOPS/actions/runs/39829618133):
  `nodeadm-config` está `failed`; `nodeadm-run`, kubelet y containerd están
  inactivos. Los errores de denegación del journal son del 20 de octubre.
  La prueba actual de `ec2:DescribeInstances` desde la instancia fue aprobada.
  Esta ejecución solo consultó diagnósticos; no reinició trabajadores ni desplegó.

## Verificación y límites

Las correcciones incluyen pruebas con Vault real aislado, supervisor SSM
simulado, TLS inválido, credenciales expiradas, cierre de sesiones, protección de
custodia/PVC y recuperación inicial. Se validan también los eventos de release,
los controles que impiden reiniciar trabajadores incorrectos y los renders Helm.

Actionlint 1.7.12 todavía no reconoce `queue`. La validación excluye únicamente
ese diagnóstico, verifica sus valores y conserva los demás errores del linter.
GitHub documenta `queue: max` en su
[control de concurrencia](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

Las pruebas locales y de CI no acreditan recuperación real de EKS/PostgreSQL,
login OIDC ni despliegue de la aplicación. Después del PR manual hacia main,
el bootstrap debe completar esas comprobaciones antes de activar CD. Los
indicadores permanecen desactivados hasta disponer de evidencia.

Tras un bootstrap correcto y la activación verificada, se necesita una release
con Helm y smoke aprobados y otra en una sesión posterior renovando solo los
tres secrets AWS. La recuperación inicial preparada aquí aún no fue ejecutada
contra AWS; sus controles se probaron sin reiniciar instancias reales.

## Seguimiento tras el PR #56

El [merge del PR #56](https://github.com/Hacket00GTFO/Boutique-Eq4-DevOPS/actions/runs/39894996500)
contiene la corrección OIDC en `main` (`9b0a5017`). Aprobaron requisitos,
pruebas unitarias, integración, seguridad y las doce publicaciones de imágenes.
CD falló esperando nodos: `registered=0, Ready=0, storage Ready=False`.
El resumen propagó ese mismo fallo. Las cinco operaciones manuales se omitieron
correctamente en el evento push.

El [diagnóstico por SSM](https://github.com/Hacket00GTFO/Boutique-Eq4-DevOPS/actions/runs/39897194426)
confirma `nodeadm-config=failed`, kubelet/containerd inactivos y permiso actual
`ec2:DescribeInstances` aprobado. El [inventario](https://github.com/Hacket00GTFO/Boutique-Eq4-DevOPS/actions/runs/39897201030)
confirma grupos ACTIVE, roles mapeados, cero nodos y EBS CSI DEGRADED por ausencia
de trabajadores. Las tres instancias fueron reemplazadas; el volumen EBS de
PostgreSQL permanece disponible.

La recuperación anterior estaba acotada al bootstrap, antes de activar CD y
sin namespace de aplicación. La corrección posterior incorpora una recuperación
separada para sesiones del Lab con datos ya verificados: solo cero nodos,
identidad de almacenamiento original, tres trabajadores propios en servicio,
fallo conocido de nodeadm y permiso actual confirmado. Comprueba otra vez esos
límites antes del reinicio. Las migraciones y restauraciones no forman parte de
esa recuperación. El workflow identifica este paso por separado de la consulta
de imágenes y explica en su resumen las omisiones de mantenimiento previstas.

Los cambios de este seguimiento se publican exclusivamente en development.
Su prueba operativa en main requiere el PR manual del responsable del repositorio.
