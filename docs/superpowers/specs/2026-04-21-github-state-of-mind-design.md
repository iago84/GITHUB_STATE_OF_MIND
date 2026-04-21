# Diseño: completar CHECKLIST + ROADMAP (Opción A)

## Contexto

El repositorio [GITHUB_STATE_OF_MIND](https://github.com/iago84/GITHUB_STATE_OF_MIND) contiene una herramienta en Python (CLI + GUI PyQt6) para auditar y estandarizar repositorios de GitHub mediante la API de GitHub.

Objetivo de este trabajo: implementar lo pendiente del ROADMAP (Fases 4–6 + criterios de éxito) y dejar el CHECKLIST y el ROADMAP marcados al 100% (tareas implementadas y verificables).

Restricciones:

- Mantener enfoque “mínimo pero completo” (MVP para cada punto).
- Evitar dependencias nuevas si no aportan valor claro; preferir stdlib (`unittest`, `logging`, `json`).
- No introducir credenciales en el repo.
- Mantener compatibilidad con el flujo actual (CLI y GUI).

## Alcance

### Incluye

- UX (GUI): resultados enriquecidos, filtrado, export más consistente, “dry-run con diff” y asistentes de README por stack.
- Calidad: tests unitarios del core con `unittest` + ejecución en CI.
- Simulación: modo offline sin llamadas a GitHub API usando fixtures.
- Observabilidad: trazas/log y “audit log” de operaciones (por repo/acción).
- Integraciones avanzadas: detectores ampliados (Kubernetes/Helm, Terraform módulos, monorepos), más workflows por stack, y plugins/convensiones por organización vía JSON.
- Documentación: checklist/roadmap actualizados y marcados, con comandos de verificación.

### No incluye

- Migración completa a paquete publicable en PyPI.
- Sistema de plugins con carga dinámica de Python (se usará configuración declarativa JSON).
- UI compleja tipo “diff viewer” sofisticado; se implementa una versión simple pero funcional.

## Arquitectura (estado actual)

- `gh_manager.py`
  - `GitHubClient`: wrapper de GitHub REST API con `urllib`.
  - `RepoAnalyzer`: auditoría “estado” (README/LICENSE/workflows/CODEOWNERS/.editorconfig/issues/branch).
  - `DeepAnalyzer`: inferencia de tecnologías a partir de tree + dependencias.
  - `Optimizer`: plan/execute para acciones base (README/LICENSE/CI/CODEOWNERS/.editorconfig/issues/protección/ramas).
  - Comandos CLI: `listar`, `estado`, `descargar`, `optimizar`, `analizar`, `mejorar`.
- `gui.py`
  - UI PyQt6 que invoca un `Worker(QThread)` para `status/optimize/analyze/improve`.

## Diseño propuesto (Opción A)

### Fase 4 — UX y Operación

1) Vistas de resultados enriquecidas

- Añadir “modo de vista” en GUI para distinguir:
  - Estado (tabla principal),
  - Plan de optimización,
  - Resultado de ejecución,
  - Análisis profundo,
  - Mejoras (con diffs si dry-run).
- Export: permitir exportar exactamente la vista activa (y no mezclar tipos de filas).

2) Dry-run con diff para “Optimizar”

- Objetivo: que al ejecutar “optimizar” en dry-run se pueda ver:
  - acciones propuestas (ya existe),
  - y, para acciones de creación/modificación de ficheros, un diff unificado.
- Implementación:
  - extraer un helper reutilizable en `gh_manager.py` para “preview” de cambios:
    - obtener texto actual (`get_file_text`) si existe,
    - calcular diff `difflib.unified_diff`,
    - devolver estructura `{path, message, diff}`.
  - en CLI:
    - cuando `optimizar --dry-run` y se activan flags de creación, emitir JSON/HTML con los diffs (y tabla “CREATE/MODIFY” si output table).
  - en GUI:
    - si el resultado incluye `diffs`, mostrar un panel “Diffs” (texto) por repo y archivo.

3) Asistentes README por stack

- Un generador simple basado en plantillas con placeholders.
- Entrada: stack seleccionado + nombre repo + owner + opcional: comandos de instalación/uso.
- Salida: preview + opción de guardar plantilla local (para reusar).
- Integración:
  - CLI: subcomando o flag (p.ej. `mejorar --generar-readme-auto` ya existe; se añade “wizard local” solo para GUI).
  - GUI: botón “Asistente README” que genera una plantilla y la pone como `tpl_readme` para la ejecución.

### Fase 5 — Calidad y Observabilidad

1) Tests unitarios (stdlib)

- Framework: `unittest`.
- Estrategia:
  - Mock de `urllib.request.urlopen` para `GitHubClient._request`.
  - Tests de:
    - `GitHubClient._paginate` (Link header con rel=next),
    - `RepoAnalyzer._summ_one` (con respuestas mockeadas),
    - `Optimizer.plan` (acciones esperadas),
    - `DeepAnalyzer._infer_techs` + detectores ampliados,
    - “preview diffs” para optimizar/mejorar (nuevo helper).
- Añadir un workflow CI en este repo para correr tests (sin token).

2) Modo simulación/offline (fixtures)

- Objetivo: ejecutar análisis/plan/diff sin llamar a GitHub.
- Diseño:
  - Añadir opción CLI: `--offline` + `--fixtures-dir`.
  - Añadir `FixtureGitHubClient` que implementa el mismo interfaz que `GitHubClient` (o un wrapper) y resuelve endpoints desde ficheros JSON.
  - Formato fixtures:
    - path por endpoint (normalizado) o por “alias” (`repos_list.json`, `repo_<name>.json`, `tree_<name>.json`, etc.).
  - GUI: checkbox “Offline (fixtures)” + selector de carpeta.

3) Observabilidad y auditoría (logs)

- `logging` con:
  - log a consola (CLI),
  - log a fichero rotativo simple (p.ej. `logs/gh_manager.log`) si se configura.
- “Audit log” estructurado:
  - fichero JSONL por ejecución (timestamped) con eventos: `{ts, owner, repo, action, ok, detail}`.
- GUI: mostrar el log en el panel inferior y permitir “Guardar log”.

### Fase 6 — Integraciones Avanzadas

1) Workflows por más stacks

- Ya existen plantillas en `plantillas/workflows/`.
- Completar:
  - selección robusta en “workflows_ai” (CLI y GUI) para Go/Rust/Java (ya aparece en CLI mejorar).
  - garantizar que `Optimizer.ensure_workflows` y `mejorar --workflows-ai` no se pisan/confunden (documentar uso recomendado).

2) Detectores ampliados

- Kubernetes/Helm:
  - detectar si existen `helm/`, `chart/`, `charts/`, `k8s/`, `kubernetes/` o YAML con `apiVersion:` y `kind:`.
- Terraform módulos:
  - detectar `modules/` o patrón `module "..."` en `.tf` (sin parseo completo).
- Monorepos:
  - refinar `node_monorepo`: además de `packages/`, detectar `pnpm-workspace.yaml`, `turbo.json`, `nx.json`, múltiples lockfiles.

3) Plugins / convenciones por organización (JSON)

- Archivo `config.json` opcional con:
  - topics globales extra,
  - reglas por nombre/patrón de repo,
  - plantillas preferidas (paths) para README/LICENSE/etc.,
  - convención de branch (target branch),
  - toggles por defecto para GUI.
- CLI: flag `--config path/to/config.json`.
- GUI: selector de config (persistir la ruta localmente si procede).

## Criterios de éxito (cómo se mide)

- Métrica “repos verdes” (ya se puede derivar del `estado`):
  - ≥95% repos con README, LICENSE, CODEOWNERS, .editorconfig.
  - ≥90% repos con CI activo, rama por defecto = main y protección habilitada.
  - ≥80% repos con topics y descripción.
- Estabilidad de auditoría:
  - concurrencia configurable y errores reportados claramente (sin crash).

## Plan de verificación (definición de “done”)

- CLI:
  - `python gh_manager.py estado --user <owner> --output json` genera reporte.
  - `python gh_manager.py optimizar ... --dry-run` produce plan + diffs donde aplique.
  - `python gh_manager.py mejorar ... --dry-run` produce diffs.
  - `python gh_manager.py ... --offline --fixtures-dir ...` funciona sin red.
- GUI:
  - cargar estado, filtrar, exportar, mostrar diffs en dry-run, ejecutar mejoras.
- Tests:
  - `python -m unittest` pasa en local.
  - Workflow CI verde en este repo.

