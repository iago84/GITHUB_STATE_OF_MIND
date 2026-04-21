# Checklist de Optimización de Repos GitHub

## Pre‑requisitos

- [x] Definir `GITHUB_TOKEN` en entorno para lectura/escritura (o usar `--token`)
- [x] (Opcional) Usar `--config config.json` para defaults y convenciones
- [x] (Opcional) Usar `--offline --fixtures-dir fixtures/` para simulación sin red
- [x] (Opcional) Activar logs/auditoría: `--log-level/--log-file/--audit-file`

## Auditoría y análisis

- [x] Ejecutar auditoría de estado (GUI o CLI)
  - CLI: `python gh_manager.py estado --user <owner> --output table`
  - Export: `python gh_manager.py estado --user <owner> --output json --out-dir reportes`
- [x] Ejecutar análisis profundo y exportar reporte
  - CLI: `python gh_manager.py analizar --user <owner> --output json --out-dir reportes`
  - GUI: “Analizar (profundo)”

## Ramas y protección

- [x] Asegurar rama `main` existente
  - CLI: `python gh_manager.py optimizar --user <owner> --asegurar-main --dry-run`
- [x] Establecer `main` como rama por defecto
  - CLI: `python gh_manager.py optimizar --user <owner> --default-main --dry-run`
- [x] Proteger rama objetivo con política mínima
  - CLI: `python gh_manager.py optimizar --user <owner> --proteger-branch --dry-run`

## Estándares del repo

- [x] Añadir/validar README (plantilla o asistente)
  - CLI: `python gh_manager.py optimizar --user <owner> --crear-readme --dry-run`
  - GUI: “Asistente README” o “Plantilla README” + “Aplicar”
- [x] Añadir/validar LICENSE adecuada
  - CLI: `python gh_manager.py optimizar --user <owner> --crear-license mit --dry-run`
  - CLI (recomendación IA): `python gh_manager.py mejorar --user <owner> --recomendar-licencia`
- [x] Añadir/validar CODEOWNERS
  - CLI: `python gh_manager.py optimizar --user <owner> --crear-codeowners --dry-run`
- [x] Añadir/validar `.editorconfig`
  - CLI: `python gh_manager.py optimizar --user <owner> --crear-editorconfig --dry-run`

## CI/CD y documentación pública

- [x] Añadir workflows CI por tecnología (AI)
  - CLI: `python gh_manager.py mejorar --user <owner> --workflows-ai --dry-run`
- [x] Configurar GitHub Pages para sitios estáticos
  - CLI: `python gh_manager.py mejorar --user <owner> --pages-static --dry-run`
- [x] Añadir `.gitignore` por tecnología
  - CLI: `python gh_manager.py mejorar --user <owner> --gitignore-auto --dry-run`
- [x] Añadir plantillas de Issues/PRs
  - CLI: `python gh_manager.py mejorar --user <owner> --issues-templates --pr-template --dry-run`

## Metadatos

- [x] Habilitar Issues
  - CLI: `python gh_manager.py optimizar --user <owner> --habilitar-issues --dry-run`
- [x] Añadir topics relevantes
  - CLI: `python gh_manager.py mejorar --user <owner> --auto-topics --dry-run`
- [x] Completar descripción/homepage
  - CLI: `python gh_manager.py mejorar --user <owner> --auto-description --pages-static --dry-run`

## Entregables y control

- [x] Exportar reportes finales (JSON/CSV/HTML)
  - `estado|analizar|optimizar|mejorar` soportan `--output json|csv|html` y `--out-dir`
- [x] Revisar límites de API y ajustar concurrencia
  - CLI: `--concurrency N` (en `estado`)
  - Config: `{"defaults":{"concurrency":4}}`
- [x] Crear PRs de housekeeping cuando convenga
  - CLI: `python gh_manager.py optimizar --user <owner> --branch chore/housekeeping --crear-pr-housekeeping`
