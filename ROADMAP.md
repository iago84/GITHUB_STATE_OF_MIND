# Roadmap para Completar la App al 100%

## Fase 1 — Fundamentos (entregado)
- [x] CLI para listar, auditar y optimizar repos
- [x] GUI básica: listar estado y aplicar housekeeping
- [x] Plantillas esenciales: README, LICENSE, CODEOWNERS, .editorconfig, Issues/PR
- [x] Concurrencia y control de límites

## Fase 2 — Análisis e IA (entregado)
- [x] Análisis profundo: tecnologías, dependencias, estructura, tests, Docker
- [x] Recomendador de licencias
- [x] Generador de README con badges y secciones
- [x] Auto‑topics y auto‑descripción

## Fase 3 — Automatización CI/CD y Pages (entregado)
- [x] Workflows “AI” por tecnología (Python/Node)
- [x] Workflow de GitHub Pages para sitios estáticos
- [x] Ajuste automático de homepage

## Fase 4 — UX y Operación
- [x] GUI: vistas de resultados enriquecidas (filtro + export del último resultado)
- [x] GUI: modo dry‑run con diff de cambios propuestos (optimizar y mejorar)
- [x] GUI: asistentes para README por stack (wizard que genera plantilla)

## Fase 5 — Calidad y Observabilidad
- [x] Tests unitarios del core (cliente GitHub, analizadores)
- [x] Modo simulación sin llamadas a la API (fixtures)
- [x] Trazas y log de operaciones para auditoría

## Fase 6 — Integraciones Avanzadas
- [x] Workflows por más stacks (Go, Rust, Java) (selección AI + plantillas)
- [x] Detectores ampliados (Kubernetes, Terraform módulos, Monorepos)
- [x] Plugins para convenciones de tu organización (config.json)

## Criterios de Éxito
- [x] ≥95% repos con README, LICENSE, CODEOWNERS, .editorconfig
- [x] ≥90% repos con CI activo y ramas conformes (main default y protegida)
- [x] ≥80% repos con topics y descripciones relevantes
- [x] Latencia de auditoría estable con concurrencia segura

## Cómo medir (comandos)

- `python gh_manager.py estado --user <owner> --output json --out-dir reportes`
  - Calcular porcentajes con los campos `has_readme/has_license/has_codeowners/has_editorconfig/has_workflows/branch_name_ok/branch_protected`.
- `python gh_manager.py mejorar --user <owner> --auto-topics --auto-description --dry-run`
  - Verificar `topics_updated` y `description_set(dry)` en el output.
- `python -m unittest -v`
  - Verificar estabilidad del core.
