# Uso

## CLI

### Requisitos

- Python 3.10+ recomendado
- Token: variable `GITHUB_TOKEN` o `--token`

### Estado (auditoría)

```bash
python gh_manager.py estado --user <owner> --output table
python gh_manager.py estado --user <owner> --output json --out-dir reportes
```

### Análisis profundo

```bash
python gh_manager.py analizar --user <owner> --output json --out-dir reportes
```

### Optimizar (plan / ejecución)

Plan con diffs (dry-run):

```bash
python gh_manager.py optimizar --user <owner> --crear-readme --crear-license mit --crear-codeowners --crear-editorconfig --asegurar-workflows --dry-run --output json --out-dir reportes
```

Ejecución (sin `--dry-run`):

```bash
python gh_manager.py optimizar --user <owner> --crear-readme --crear-license mit --crear-codeowners --crear-editorconfig --asegurar-workflows
```

### Mejorar (IA)

```bash
python gh_manager.py mejorar --user <owner> --auto-topics --auto-description --gitignore-auto --issues-templates --pr-template --workflows-ai --pages-static --dry-run
```

### Configuración (`--config`)

```bash
python gh_manager.py --config config.example.json estado --user <owner>
python gh_manager.py --config config.example.json optimizar --user <owner> --dry-run
python gh_manager.py --config config.example.json mejorar --user <owner> --dry-run
```

### Offline (fixtures)

```bash
python gh_manager.py --offline --fixtures-dir fixtures/sample listar --user iago84
python gh_manager.py --offline --fixtures-dir fixtures/sample estado --user iago84
```

### Logging y auditoría

```bash
python gh_manager.py --log-level DEBUG --log-file reportes/gh_manager.log --audit-file reportes/audit.jsonl estado --user <owner>
```

## GUI

```bash
python gui.py
```

- “Offline” + “Fixtures” permite ejecutar sin red.
- “Config” permite cargar `config.json` y aplicar defaults.
- “Asistente README” genera una plantilla y la reutiliza como plantilla en “Optimizar”.
- “Guardar log” guarda el panel de logs a un fichero.

