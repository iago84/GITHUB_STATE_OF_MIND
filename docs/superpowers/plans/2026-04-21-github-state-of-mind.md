# GitHub State Of Mind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completar el CHECKLIST y el ROADMAP implementando Fases 4–6 (UX, calidad/observabilidad, integraciones avanzadas) y dejar documentación marcable al 100% con verificación reproducible.

**Architecture:** Añadir capas mínimas alrededor del core existente (`gh_manager.py`) para: logging + audit, modo offline por fixtures, diffs en dry-run para optimizar, y configuración por `config.json`. Extender GUI (`gui.py`) para soportar vistas/export/diffs sin reescritura completa.

**Tech Stack:** Python (stdlib: argparse/json/logging/unittest/difflib), PyQt6 (GUI), GitHub REST API via urllib.

---

## Estructura de cambios

**Modificar**
- `gh_manager.py` (core: config, offline fixtures, logging/audit, diffs en optimizar, detectores ampliados, CLI flags)
- `gui.py` (UX: vistas, export, diffs, offline, logs)
- `CHECKLIST.md` (marcar al 100% + comandos de verificación)
- `ROADMAP.md` (marcar Fases 4–6 + criterios con verificación)

**Crear**
- `.github/workflows/ci.yml` (CI de este repo: ejecutar unit tests)
- `tests/test_gh_manager_unittest.py` (tests unitarios con unittest)
- `fixtures/sample/` (fixtures mínimos de ejemplo para offline)
- `config.example.json` (ejemplo de configuración)
- `docs/USO.md` (guía rápida CLI/GUI, offline, config, logs)

---

### Task 1: Baseline de tests + CI para este repo

**Files:**
- Create: `/workspace/GITHUB_STATE_OF_MIND/tests/test_gh_manager_unittest.py`
- Create: `/workspace/GITHUB_STATE_OF_MIND/.github/workflows/ci.yml`

- [ ] **Step 1: Crear tests iniciales (failing)**

```python
import io
import json
import unittest
from unittest.mock import patch

import gh_manager


class FakeHTTPResponse:
    def __init__(self, status, headers, body: bytes):
        self._status = status
        self.headers = headers
        self._body = body
        self.fp = io.BytesIO(body)

    def getcode(self):
        return self._status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestGitHubClientPagination(unittest.TestCase):
    def test_paginate_follows_next_link(self):
        pages = [
            (
                200,
                {"Link": '<https://api.github.com/users/x/repos?page=2>; rel="next"'},
                json.dumps([{"name": "a"}]).encode(),
            ),
            (200, {}, json.dumps([{"name": "b"}]).encode()),
        ]
        calls = {"i": 0}

        def fake_urlopen(req, timeout=60):
            i = calls["i"]
            calls["i"] += 1
            status, headers, body = pages[i]
            return FakeHTTPResponse(status, headers, body)

        with patch("urllib.request.urlopen", new=fake_urlopen):
            gh = gh_manager.GitHubClient(token=None)
            items = gh._paginate("https://api.github.com/users/x/repos?per_page=100&type=all")
            self.assertEqual([i["name"] for i in items], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Ejecutar tests y confirmar que falla si falta wiring**

Run: `python -m unittest -v`
Expected: FAIL si el import o rutas no están correctas (ajustar hasta que sea PASS).

- [ ] **Step 3: Añadir workflow CI**

```yaml
name: CI
on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Run unit tests
        run: python -m unittest -v
```

- [ ] **Step 4: Verificar CI localmente**

Run: `python -m unittest -v`
Expected: PASS.

---

### Task 2: Logging + audit log (CLI/GUI) sin dependencias

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gh_manager.py`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gui.py`

- [ ] **Step 1: Añadir configuración de logging en `gh_manager.py`**
  - Añadir helper `setup_logging(log_level, log_file)` y usar `logging.getLogger("gh_manager")`.
  - Loggear requests/respuestas de forma segura (sin incluir token).

- [ ] **Step 2: Añadir AuditLogger (JSONL)**
  - Nuevo helper `AuditLogger(path)` con `emit(event: dict)` y `close()`.
  - Emitir eventos en acciones que mutan repos: create/update file, topics, description, issues, branch ops.

- [ ] **Step 3: Exponer flags CLI**
  - En `parse_args`: `--log-level`, `--log-file`, `--audit-file`.
  - En `main`: llamar a `setup_logging` y pasar `audit` al flujo (por args).

- [ ] **Step 4: GUI muestra logs**
  - En `Worker.progress` emitir mensajes de logging relevantes (o enviar desde core).
  - Añadir botón “Guardar log” (guardar contenido de QTextEdit actual).

- [ ] **Step 5: Tests mínimos**
  - Añadir test que asegura que `AuditLogger` escribe JSONL válido.

---

### Task 3: Modo offline por fixtures (sin red)

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gh_manager.py`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gui.py`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/README.md`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/users_iago84_repos.json`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/repos_iago84_GITHUB_STATE_OF_MIND.json`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/repos_iago84_GITHUB_STATE_OF_MIND_languages.json`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/repos_iago84_GITHUB_STATE_OF_MIND_topics.json`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/repos_iago84_GITHUB_STATE_OF_MIND_git_ref_heads_main.json`
- Create: `/workspace/GITHUB_STATE_OF_MIND/fixtures/sample/repos_iago84_GITHUB_STATE_OF_MIND_git_trees_<sha>.json`

- [ ] **Step 1: Definir normalización de claves de fixture**
  - Clave derivada del endpoint: método + path + query normalizados.
  - Guardar en fichero `.json` dentro de fixtures dir.

- [ ] **Step 2: Implementar `FixtureGitHubClient`**
  - Debe soportar al menos endpoints usados por:
    - `estado`,
    - `analizar`,
    - `optimizar --dry-run`,
    - `mejorar --dry-run`.

- [ ] **Step 3: Añadir flags CLI**
  - `--offline` y `--fixtures-dir`.
  - En `main`, construir `gh` como fixture o real.

- [ ] **Step 4: GUI**
  - Checkbox “Offline (fixtures)” + selector de directorio.
  - Pasar params al Worker, que construye el cliente correcto.

- [ ] **Step 5: Tests**
  - Test de “offline list_repos” leyendo el fixture sample.

---

### Task 4: Dry-run con diff para `optimizar`

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gh_manager.py`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gui.py`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/tests/test_gh_manager_unittest.py`

- [ ] **Step 1: Extraer helper de diff**
  - `unified_diff(old_text, new_text, path) -> str`.

- [ ] **Step 2: Hacer que `Optimizer.execute` soporte `dry_run=True`**
  - En lugar de escribir, crear `diffs` por repo para acciones:
    - README, LICENSE, workflows, CODEOWNERS, .editorconfig.

- [ ] **Step 3: Extender CLI `optimizar`**
  - Si `--dry-run`, devolver filas con `diffs` además de `actions`.
  - Soportar `--output json/csv/html` con diffs (CSV: incluir solo “resumen” sin diff completo).

- [ ] **Step 4: GUI renderiza diffs**
  - Si rows contienen `diffs`, añadirlos al panel log y permitir copiar/guardar.

- [ ] **Step 5: Tests**
  - Test de que dry-run genera diff “CREATE” cuando el archivo no existe.

---

### Task 5: Detectores ampliados (Kubernetes/Helm/Terraform/Monorepo)

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gh_manager.py`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/tests/test_gh_manager_unittest.py`

- [ ] **Step 1: Ampliar `_infer_techs` y señales por paths**
  - Helm/K8s: paths con `helm/`, `charts/`, `k8s/`, `kubernetes/`, `Chart.yaml`, `values.yaml`.
  - Terraform módulos: `modules/` y `.tf`.
  - Monorepo node: `pnpm-workspace.yaml`, `turbo.json`, `nx.json`, múltiples `package.json`, `packages/`.

- [ ] **Step 2: Tests**
  - Tests unitarios alimentando `paths` y verificando techs detectadas.

---

### Task 6: Plugins de convención por `config.json` (CLI + GUI)

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gh_manager.py`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gui.py`
- Create: `/workspace/GITHUB_STATE_OF_MIND/config.example.json`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/tests/test_gh_manager_unittest.py`

- [ ] **Step 1: Definir esquema mínimo**
  - `defaults`: flags por defecto (auto_topics, workflows_ai, pages_static, etc.).
  - `topics_extra`: lista global.
  - `target_branch`: string.
  - `codeowners`: override plantilla.
  - `readme_template_path` etc (opcionales).

- [ ] **Step 2: Implementar loader**
  - `load_config(path) -> dict` con validación básica.
  - Exponer `--config` en CLI.

- [ ] **Step 3: Aplicar config**
  - CLI: merge defaults con args explícitos (args explícitos ganan).
  - “topics”: al hacer auto-topics, añadir también `topics_extra`.

- [ ] **Step 4: GUI**
  - Botón/selector “Config” y aplicar defaults al iniciar/cargar.

- [ ] **Step 5: Tests**
  - Test de merge de defaults.

---

### Task 7: Asistente README por stack (GUI)

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/gui.py`
- Create: `/workspace/GITHUB_STATE_OF_MIND/docs/USO.md`

- [ ] **Step 1: UI mínima**
  - Botón “Asistente README”.
  - Diálogo con selector de stack (Python/Node/Go/Rust/Java/Static) + preview.

- [ ] **Step 2: Generación**
  - Basado en plantillas existentes (`plantillas/README.md`) + placeholders.
  - Guardar a fichero local y setear `tpl_readme`.

- [ ] **Step 3: Documentar en USO.md**

---

### Task 8: Documentación final + marcar CHECKLIST/ROADMAP al 100%

**Files:**
- Modify: `/workspace/GITHUB_STATE_OF_MIND/CHECKLIST.md`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/ROADMAP.md`
- Modify: `/workspace/GITHUB_STATE_OF_MIND/docs/USO.md`

- [ ] **Step 1: CHECKLIST**
  - Marcar ítems implementados.
  - Añadir “cómo verificar” (CLI/GUI) al final.

- [ ] **Step 2: ROADMAP**
  - Marcar Fase 4–6 como entregado.
  - Marcar criterios y explicar cómo medir (usando `estado`/reportes).

- [ ] **Step 3: Verificación**
  - `python -m unittest -v`
  - `python gh_manager.py --help` y subcomandos
  - Ejemplo offline con fixtures sample.

---

## Ejecución

El usuario ya eligió “implementa y codifica”: ejecutar en esta misma sesión con enfoque “inline execution”.

