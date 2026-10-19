# Sincronización de ramas del equipo

El workflow `sync-branches.yaml` fusiona `development` en los destinos explícitos
`David` y `Norberto` al integrar cualquier PR en `development`. También admite
ejecución manual cuando GitHub tenga disponible esa versión del workflow.
Incluye cambios de documentación, Terraform y Helm. Las ejecuciones se serializan
entre PRs y cada una utiliza el último estado remoto de `development`.

El origen de una rama no se deduce de si su ancestro común pertenece a `main`:
esa condición cambia al promover `development` a producción. Los destinos se
configuran en la invocación del script dentro del workflow.

Cada merge conserva los commits propios y se prepara en un worktree temporal.
Los pushes son normales, sin sobrescribir el historial. Si hay conflictos, falta
una rama o se rechaza un push, se informa en el resumen y el job termina fallido
después de intentar los demás destinos. Los destinos que ya contienen el origen
no generan nuevos commits. El checkout principal conserva su rama y sus archivos.

Vista previa reproducible, sin cambios remotos:

```bash
python3 scripts/branch-sync/sync.py --target David --target Norberto
```

Para aplicar, agregar `--push`; requiere permisos de escritura y cumplir las
reglas de protección de las ramas. `main`, `development`, `release/*`, `sync/*`
y `gh-pages` están protegidas como destinos por el script.

Pruebas con repositorios Git locales desechables:

```bash
python3 -m unittest discover -s scripts/branch-sync/tests -v
```

Cubren promoción a main, preservación de commits, idempotencia, conflictos,
rechazo del servidor, rama ausente, historial independiente y vista previa.
