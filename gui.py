import sys
import time
import json
import difflib
from pathlib import Path
from typing import Dict, List
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QSpinBox, QFileDialog, QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox, QGroupBox, QTextEdit
from gh_manager import AuditLogger, FixtureGitHubClient, GitHubClient, RepoAnalyzer, Optimizer, DeepAnalyzer, LicenseAdvisor, ReadmeGenerator, write_json, write_csv, write_html


class Worker(QThread):
    progress = pyqtSignal(str)
    status_ready = pyqtSignal(list)
    result_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, mode: str, params: Dict):
        super().__init__()
        self.mode = mode
        self.params = params

    def run(self):
        audit = None
        try:
            cfg = {}
            cfg_path = self.params.get("config")
            if cfg_path and Path(cfg_path).exists():
                try:
                    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
                except Exception:
                    cfg = {}
            audit_path = self.params.get("audit_file")
            audit = AuditLogger(audit_path) if audit_path else None
            if self.params.get("offline"):
                gh = FixtureGitHubClient(self.params.get("fixtures_dir", "fixtures/sample"), token=self.params.get("token"), audit=audit)
            else:
                gh = GitHubClient(token=self.params.get("token"), audit=audit)
            owner = self.params["owner"]
            owner_type = self.params["owner_type"]
            visibility = self.params.get("visibility", "all")
            concurrency = int(self.params.get("concurrency", 4))
            target_branch = self.params.get("target_branch", "main")
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=visibility, archived=None)
            if self.mode == "status":
                analyzer = RepoAnalyzer(gh, owner, target_branch_name=target_branch, concurrency=concurrency)
                rows = analyzer.summarize(repos)
                self.status_ready.emit(rows)
                return
            if self.mode == "optimize":
                selected = self.params.get("selected_names")
                subset = [r for r in repos if r.get("name") in selected] if selected else repos
                analyzer = RepoAnalyzer(gh, owner, target_branch_name=target_branch, concurrency=concurrency)
                status_rows = analyzer.summarize(subset)
                templates: Dict[str, str] = {}
                cfg_tpl = cfg.get("templates") if isinstance(cfg.get("templates"), dict) else {}
                if isinstance(cfg_tpl, dict):
                    for k in ("readme", "license", "codeowners", "editorconfig", "workflow_ci"):
                        v = cfg_tpl.get(k)
                        if v and k not in templates and Path(v).exists():
                            templates[k] = Path(v).read_text(encoding="utf-8")
                if self.params.get("tpl_readme"):
                    p = Path(self.params["tpl_readme"])
                    if p.exists():
                        templates["readme"] = p.read_text(encoding="utf-8")
                if self.params.get("tpl_license"):
                    p = Path(self.params["tpl_license"])
                    if p.exists():
                        templates["license"] = p.read_text(encoding="utf-8")
                if self.params.get("tpl_codeowners"):
                    p = Path(self.params["tpl_codeowners"])
                    if p.exists():
                        templates["codeowners"] = p.read_text(encoding="utf-8")
                if self.params.get("tpl_editorconfig"):
                    p = Path(self.params["tpl_editorconfig"])
                    if p.exists():
                        templates["editorconfig"] = p.read_text(encoding="utf-8")
                base = Path(__file__).parent / "plantillas"
                if "readme" not in templates and (base / "README.md").exists():
                    templates["readme"] = (base / "README.md").read_text(encoding="utf-8")
                if "license" not in templates and (base / "LICENSE_MIT.txt").exists():
                    templates["license"] = (base / "LICENSE_MIT.txt").read_text(encoding="utf-8")
                if "codeowners" not in templates and (base / "CODEOWNERS").exists():
                    templates["codeowners"] = (base / "CODEOWNERS").read_text(encoding="utf-8")
                if "editorconfig" not in templates and (base / ".editorconfig").exists():
                    templates["editorconfig"] = (base / ".editorconfig").read_text(encoding="utf-8")
                if (base / "ci.yml").exists():
                    templates["workflow_ci"] = (base / "ci.yml").read_text(encoding="utf-8")
                optimizer = Optimizer(gh, owner, target_branch_name=target_branch, templates=templates)
                plans = optimizer.plan(
                    status_rows,
                    self.params.get("crear_readme", False),
                    self.params.get("crear_license", None),
                    self.params.get("asegurar_workflows", False),
                    self.params.get("habilitar_issues", False),
                    create_codeowners=self.params.get("crear_codeowners", False),
                    create_editorconfig=self.params.get("crear_editorconfig", False),
                    protect_branch=self.params.get("proteger_branch", False),
                    rename_branch=self.params.get("renombrar_branch", False),
                    ensure_main=self.params.get("asegurar_main", False),
                    default_main=self.params.get("default_main", False),
                )
                if self.params.get("dry_run", True):
                    want_diffs = bool(self.params.get("crear_readme") or self.params.get("crear_license") or self.params.get("asegurar_workflows") or self.params.get("crear_codeowners") or self.params.get("crear_editorconfig"))
                    if want_diffs and plans:
                        default_branches = {r.get("name"): (r.get("default_branch") or target_branch) for r in status_rows if isinstance(r, dict) and r.get("name")}
                        self.result_ready.emit(optimizer.preview(plans, default_branches))
                    else:
                        self.result_ready.emit(plans)
                    return
                if self.params.get("crear_pr", False) and self.params.get("branch"):
                    base_branch = status_rows[0].get("default_branch") or target_branch if status_rows else target_branch
                    results = optimizer.execute(plans, branch=self.params.get("branch"), open_pr=True, base_branch=base_branch)
                else:
                    results = optimizer.execute(plans, branch=None)
                if self.params.get("crear_issue", False):
                    for r in results:
                        if r.get("executed"):
                            gh.create_issue(owner, r["name"], "Housekeeping", "Se ejecutan acciones: " + ", ".join(r.get("executed", [])))
                self.result_ready.emit(results)
                return
            if self.mode == "analyze":
                selected = self.params.get("selected_names")
                subset = [r for r in repos if r.get("name") in selected] if selected else repos
                deep = DeepAnalyzer(gh, owner)
                rows = [deep.analyze(r) for r in subset]
                self.result_ready.emit(rows)
                return
            if self.mode == "improve":
                selected = self.params.get("selected_names")
                subset = [r for r in repos if r.get("name") in selected] if selected else repos
                deep = DeepAnalyzer(gh, owner)
                advisor = LicenseAdvisor()
                readme_gen = ReadmeGenerator(owner)
                base_actions = {
                    "auto_topics": self.params.get("auto_topics", False),
                    "auto_description": self.params.get("auto_description", False),
                    "gitignore_auto": self.params.get("gitignore_auto", False),
                    "issues_templates": self.params.get("issues_templates", False),
                    "pr_template": self.params.get("pr_template", False),
                    "generar_readme_auto": self.params.get("generar_readme_auto", False),
                    "forzar_readme": self.params.get("forzar_readme", False),
                    "recomendar_licencia": self.params.get("recomendar_licencia", False),
                    "aplicar_licencia": self.params.get("aplicar_licencia", False),
                }
                base = Path(__file__).parent / "plantillas"
                results = []
                for r in subset:
                    name = r.get("name")
                    analysis = deep.analyze(r)
                    executed = []
                    diffs = []
                    def preview_or_apply(path: str, content: str, message: str) -> bool:
                        if self.params.get("dry_run", False):
                            old = gh.get_file_text(owner, name, path, r.get("default_branch")) or ""
                            new = content
                            diff_text = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
                            diffs.append({"path": path, "message": message, "diff": diff_text or f"CREATE {path}"})
                            return True
                        return gh.create_or_update_file(owner, name, path, content, message)
                    if base_actions["recomendar_licencia"]:
                        rec = advisor.recommend(r, analysis)
                        executed.append(f"license_recommended:{rec}")
                        if base_actions["aplicar_licencia"]:
                            if rec == "proprietary":
                                lic_path = base / "LICENSE_PROPRIETARY.txt"
                                if lic_path.exists() and not gh.has_license(owner, name):
                                    txt = lic_path.read_text(encoding="utf-8")
                                    y = time.gmtime().tm_year
                                    txt = txt.replace("{{OWNER}}", owner).replace("{{YEAR}}", str(y))
                                    if preview_or_apply("LICENSE", txt, "Add proprietary LICENSE"):
                                        executed.append("license_applied:proprietary")
                            elif rec in ("mit", "apache-2.0", "gpl-3.0"):
                                y = time.gmtime().tm_year
                                txt = Optimizer(gh, owner)._license_text(rec, name).replace("{{OWNER}}", owner).replace("{{YEAR}}", str(y))
                                if not gh.has_license(owner, name):
                                    if preview_or_apply("LICENSE", txt, "Add LICENSE"):
                                        executed.append(f"license_applied:{rec}")
                    if base_actions["auto_topics"]:
                        add_topics = set(analysis.get("techs", []))
                        cur_topics = set(gh.get_topics(owner, name))
                        extra = cfg.get("topics_extra") if isinstance(cfg.get("topics_extra"), list) else []
                        merged = sorted(cur_topics | add_topics | set([t for t in extra if isinstance(t, str) and t.strip()]))
                        if merged != list(cur_topics):
                            if gh.set_topics(owner, name, merged):
                                executed.append("topics_updated")
                    if base_actions["auto_description"] and not (r.get("description") or "").strip():
                        langs = ", ".join(sorted((analysis.get("languages") or {}).keys()))
                        desc = f"{name}: {langs}"
                        if self.params.get("dry_run", False):
                            executed.append("description_set(dry)")
                        elif gh.update_repo_description(owner, name, description=desc):
                            executed.append("description_set")
                    if base_actions["gitignore_auto"] and not gh.has_file(owner, name, ".gitignore", r.get("default_branch")):
                        gi_content = None
                        langs = set((analysis.get("languages") or {}).keys())
                        if "Python" in langs and (base / ".gitignore_python").exists():
                            gi_content = (base / ".gitignore_python").read_text(encoding="utf-8")
                        elif ("JavaScript" in langs or "TypeScript" in langs) and (base / ".gitignore_node").exists():
                            gi_content = (base / ".gitignore_node").read_text(encoding="utf-8")
                        if gi_content:
                            if preview_or_apply(".gitignore", gi_content, "Add .gitignore"):
                                executed.append("gitignore_added")
                    if base_actions["issues_templates"] and not gh.has_file(owner, name, ".github/ISSUE_TEMPLATE/bug_report.md", r.get("default_branch")):
                        p = base / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
                        if p.exists():
                            if preview_or_apply(".github/ISSUE_TEMPLATE/bug_report.md", p.read_text(encoding="utf-8"), "Add bug report template"):
                                executed.append("issue_template_bug")
                        p2 = base / ".github" / "ISSUE_TEMPLATE" / "feature_request.md"
                        if p2.exists() and not gh.has_file(owner, name, ".github/ISSUE_TEMPLATE/feature_request.md", r.get("default_branch")):
                            if preview_or_apply(".github/ISSUE_TEMPLATE/feature_request.md", p2.read_text(encoding="utf-8"), "Add feature request template"):
                                executed.append("issue_template_feature")
                    if base_actions["pr_template"] and not gh.has_file(owner, name, ".github/PULL_REQUEST_TEMPLATE.md", r.get("default_branch")):
                        p = base / ".github" / "PULL_REQUEST_TEMPLATE.md"
                        if p.exists():
                            if preview_or_apply(".github/PULL_REQUEST_TEMPLATE.md", p.read_text(encoding="utf-8"), "Add PR template"):
                                executed.append("pr_template")
                    if base_actions["generar_readme_auto"]:
                        exists = gh.has_readme(owner, name)
                        if base_actions["forzar_readme"] or not exists:
                            content = readme_gen.build(r, analysis)
                            if preview_or_apply("README.md", content, "Generate README"):
                                executed.append("readme_generated")
                    if self.params.get("workflows_ai", False) and not gh.has_workflows(owner, name):
                        wf = None
                        langs = set((analysis.get("languages") or {}).keys())
                        wfdir = base / "workflows"
                        if analysis.get("node_monorepo") and (wfdir / "ci_monorepo_node.yml").exists():
                            wf = (wfdir / "ci_monorepo_node.yml").read_text(encoding="utf-8")
                        elif "Go" in langs and (wfdir / "ci_go.yml").exists():
                            wf = (wfdir / "ci_go.yml").read_text(encoding="utf-8")
                        elif "Rust" in langs and (wfdir / "ci_rust.yml").exists():
                            wf = (wfdir / "ci_rust.yml").read_text(encoding="utf-8")
                        elif "Java" in langs and (wfdir / "ci_java.yml").exists():
                            wf = (wfdir / "ci_java.yml").read_text(encoding="utf-8")
                        elif "Python" in langs and (wfdir / "ci_python.yml").exists():
                            wf = (wfdir / "ci_python.yml").read_text(encoding="utf-8")
                        elif ("JavaScript" in langs or "TypeScript" in langs) and (wfdir / "ci_node.yml").exists():
                            wf = (wfdir / "ci_node.yml").read_text(encoding="utf-8")
                        if wf:
                            if preview_or_apply(".github/workflows/ci.yml", wf, "Add CI workflow AI"):
                                executed.append("workflow_ai_added")
                    if self.params.get("pages_static", False):
                        pages_wf_path = ".github/workflows/pages.yml"
                        has_pages_wf = gh.has_file(owner, name, pages_wf_path, r.get("default_branch"))
                        if not has_pages_wf and (base / "workflows" / "pages_static.yml").exists():
                            wf = (base / "workflows" / "pages_static.yml").read_text(encoding="utf-8")
                            if preview_or_apply(pages_wf_path, wf, "Add Pages workflow"):
                                executed.append("pages_workflow_added")
                        root = "docs"
                        index_path = f"{root}/index.html"
                        if not gh.has_file(owner, name, index_path, r.get("default_branch")):
                            html = (base / "pages" / "docsify_index.html").read_text(encoding="utf-8") if (base / "pages" / "docsify_index.html").exists() else f"<!doctype html><meta charset='utf-8'><title>{name}</title><h1>{name}</h1><p>Publicado con GitHub Pages.</p>"
                            if preview_or_apply(index_path, html, "Add Pages public index"):
                                executed.append("pages_index_added")
                        sb_path = f"{root}/_sidebar.md"
                        if not gh.has_file(owner, name, sb_path, r.get("default_branch")) and (base / "pages" / "docsify_sidebar.md").exists():
                            if preview_or_apply(sb_path, (base / "pages" / "docsify_sidebar.md").read_text(encoding="utf-8"), "Add Docsify _sidebar.md"):
                                executed.append("pages_docsify_sidebar")
                        readme_path = f"{root}/README.md"
                        if not gh.has_file(owner, name, readme_path, r.get("default_branch")) and (base / "pages" / "docsify_readme.md").exists():
                            if preview_or_apply(readme_path, (base / "pages" / "docsify_readme.md").read_text(encoding="utf-8"), "Add Docsify README.md"):
                                executed.append("pages_docsify_readme")
                        homepage = r.get("homepage") or ""
                        if not homepage:
                            url = f"https://{owner}.github.io/{name}"
                            if self.params.get("dry_run", False):
                                executed.append("homepage_set(dry)")
                            elif gh.update_repo_description(owner, name, homepage=url):
                                executed.append("homepage_set")
                    row = {"name": name, "executed": executed}
                    if self.params.get("dry_run", False):
                        row["diffs"] = diffs
                    results.append(row)
                self.result_ready.emit(results)
                return
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if audit:
                audit.close()


class ReadmeWizard(QDialog):
    def __init__(self, owner: str, repo: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Asistente README")
        self.saved_path = None
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Stack"))
        self.stack = QComboBox()
        self.stack.addItems(["Python", "Node", "Go", "Rust", "Java", "Static"])
        top.addWidget(self.stack)
        v.addLayout(top)
        self.preview = QTextEdit()
        v.addWidget(self.preview)
        btns = QHBoxLayout()
        self.btn_save = QPushButton("Guardar")
        self.btn_close = QPushButton("Cerrar")
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_close)
        v.addLayout(btns)
        self._owner = owner or "OWNER"
        self._repo = repo or "REPO"
        self.stack.currentTextChanged.connect(self.render)
        self.btn_save.clicked.connect(self.save)
        self.btn_close.clicked.connect(self.reject)
        self.render()

    def render(self):
        stack = self.stack.currentText()
        repo = self._repo
        owner = self._owner
        install = ""
        run_cmd = ""
        test_cmd = ""
        if stack == "Python":
            install = "python -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt"
            run_cmd = "python main.py"
            test_cmd = "python -m unittest -v"
        elif stack == "Node":
            install = "npm install"
            run_cmd = "npm run dev"
            test_cmd = "npm test"
        elif stack == "Go":
            install = "go mod download"
            run_cmd = "go run ./..."
            test_cmd = "go test ./..."
        elif stack == "Rust":
            install = "cargo build"
            run_cmd = "cargo run"
            test_cmd = "cargo test"
        elif stack == "Java":
            install = "mvn -q -DskipTests package"
            run_cmd = "mvn -q exec:java"
            test_cmd = "mvn -q test"
        else:
            install = "N/A"
            run_cmd = "Abrir en navegador"
            test_cmd = "N/A"
        txt = f"""# {repo}

## Qué es

Repositorio de {owner}.

## Instalación

```bash
{install}
```

## Uso

```bash
{run_cmd}
```

## Tests

```bash
{test_cmd}
```
"""
        self.preview.setPlainText(txt)

    def save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Guardar README", "README.md", "Markdown (*.md);;Todos (*)")
        if p:
            Path(p).write_text(self.preview.toPlainText(), encoding="utf-8")
            self.saved_path = p
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitHub Manager GUI")
        cw = QWidget()
        self.setCentralWidget(cw)
        v = QVBoxLayout(cw)

        top = QHBoxLayout()
        v.addLayout(top)
        top.addWidget(QLabel("Token"))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        top.addWidget(self.token)
        self.owner_type = QComboBox()
        self.owner_type.addItems(["user", "org"])
        top.addWidget(self.owner_type)
        self.owner = QLineEdit()
        self.owner.setPlaceholderText("owner")
        top.addWidget(self.owner)
        self.visibility = QComboBox()
        self.visibility.addItems(["all", "public", "private"])
        top.addWidget(self.visibility)
        top.addWidget(QLabel("Concurrencia"))
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 32)
        self.concurrency.setValue(4)
        top.addWidget(self.concurrency)
        top.addWidget(QLabel("Rama objetivo"))
        self.target_branch = QLineEdit("main")
        top.addWidget(self.target_branch)
        self.cb_offline = QCheckBox("Offline")
        top.addWidget(self.cb_offline)
        self.fixtures_dir = QLineEdit("fixtures/sample")
        top.addWidget(self.fixtures_dir)
        self.btn_fixtures = QPushButton("Fixtures")
        top.addWidget(self.btn_fixtures)
        self.config = QLineEdit()
        self.config.setPlaceholderText("config.json")
        top.addWidget(self.config)
        self.btn_config = QPushButton("Config")
        top.addWidget(self.btn_config)
        self.btn_status = QPushButton("Listar estado")
        top.addWidget(self.btn_status)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(["Sel", "Repo", "Privado", "Archivado", "Rama", "Nombre OK", "Protegida", "README", "LICENSE", "Workflows"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        v.addWidget(self.table)

        actions_box = QGroupBox("Acciones")
        vv = QVBoxLayout(actions_box)
        g1 = QHBoxLayout()
        self.cb_readme = QCheckBox("Crear README")
        self.cb_license = QCheckBox("Crear LICENSE (MIT)")
        self.cb_workflows = QCheckBox("Asegurar Workflows")
        self.cb_issues = QCheckBox("Habilitar Issues")
        self.cb_codeowners = QCheckBox("Crear CODEOWNERS")
        self.cb_editorconfig = QCheckBox("Crear .editorconfig")
        g1.addWidget(self.cb_readme)
        g1.addWidget(self.cb_license)
        g1.addWidget(self.cb_workflows)
        g1.addWidget(self.cb_issues)
        g1.addWidget(self.cb_codeowners)
        g1.addWidget(self.cb_editorconfig)
        vv.addLayout(g1)
        g2 = QHBoxLayout()
        self.cb_protect = QCheckBox("Proteger rama objetivo")
        self.cb_rename = QCheckBox("Renombrar rama a objetivo")
        self.cb_main = QCheckBox("Asegurar rama main")
        self.cb_default_main = QCheckBox("Establecer main por defecto")
        g2.addWidget(self.cb_protect)
        g2.addWidget(self.cb_rename)
        g2.addWidget(self.cb_main)
        g2.addWidget(self.cb_default_main)
        vv.addLayout(g2)
        g3 = QHBoxLayout()
        self.cb_dry = QCheckBox("Dry-run")
        self.cb_dry.setChecked(True)
        self.cb_issue = QCheckBox("Crear Issue")
        self.cb_pr = QCheckBox("Crear PR")
        self.branch_pr = QLineEdit()
        self.branch_pr.setPlaceholderText("rama PR, ej. chore/housekeeping")
        g3.addWidget(self.cb_dry)
        g3.addWidget(self.cb_issue)
        g3.addWidget(self.cb_pr)
        g3.addWidget(self.branch_pr)
        vv.addLayout(g3)
        g4 = QHBoxLayout()
        self.btn_tpl_readme = QPushButton("Plantilla README")
        self.btn_readme_wizard = QPushButton("Asistente README")
        self.btn_tpl_license = QPushButton("Plantilla LICENSE")
        self.btn_tpl_codeowners = QPushButton("Plantilla CODEOWNERS")
        self.btn_tpl_editorconfig = QPushButton("Plantilla .editorconfig")
        g4.addWidget(self.btn_tpl_readme)
        g4.addWidget(self.btn_readme_wizard)
        g4.addWidget(self.btn_tpl_license)
        g4.addWidget(self.btn_tpl_codeowners)
        g4.addWidget(self.btn_tpl_editorconfig)
        vv.addLayout(g4)
        v.addWidget(actions_box)

        export = QHBoxLayout()
        self.btn_export_json = QPushButton("Exportar JSON")
        self.btn_export_csv = QPushButton("Exportar CSV")
        self.btn_export_html = QPushButton("Exportar HTML")
        self.btn_save_log = QPushButton("Guardar log")
        self.btn_apply = QPushButton("Aplicar")
        export.addWidget(self.btn_export_json)
        export.addWidget(self.btn_export_csv)
        export.addWidget(self.btn_export_html)
        export.addWidget(self.btn_save_log)
        export.addWidget(self.btn_apply)
        v.addLayout(export)

        improve_box = QGroupBox("Análisis y Mejora")
        iv = QVBoxLayout(improve_box)
        i1 = QHBoxLayout()
        self.cb_auto_topics = QCheckBox("Auto-topics")
        self.cb_auto_topics.setChecked(True)
        self.cb_auto_desc = QCheckBox("Auto-descripción")
        self.cb_auto_desc.setChecked(True)
        self.cb_gitignore = QCheckBox(".gitignore auto")
        self.cb_gitignore.setChecked(True)
        self.cb_issue_tpl = QCheckBox("Plantillas Issues")
        self.cb_pr_tpl = QCheckBox("Plantilla PR")
        self.cb_readme_auto = QCheckBox("Generar README auto")
        self.cb_readme_auto.setChecked(True)
        self.cb_readme_force = QCheckBox("Forzar README")
        self.cb_lic_rec = QCheckBox("Recomendar licencia")
        self.cb_lic_apply = QCheckBox("Aplicar licencia")
        self.cb_wf_ai = QCheckBox("Workflows AI")
        self.cb_wf_ai.setChecked(True)
        self.cb_pages = QCheckBox("Pages estáticas (Docsify)")
        self.cb_pages.setChecked(True)
        for w in [self.cb_auto_topics, self.cb_auto_desc, self.cb_gitignore, self.cb_issue_tpl, self.cb_pr_tpl, self.cb_readme_auto, self.cb_readme_force, self.cb_lic_rec, self.cb_lic_apply]:
            i1.addWidget(w)
        i1.addWidget(self.cb_wf_ai)
        i1.addWidget(self.cb_pages)
        iv.addLayout(i1)
        i2 = QHBoxLayout()
        self.btn_analyze = QPushButton("Analizar (profundo)")
        self.btn_improve = QPushButton("Mejorar")
        i2.addWidget(self.btn_analyze)
        i2.addWidget(self.btn_improve)
        iv.addLayout(i2)
        i3 = QHBoxLayout()
        self.cb_dry_improve = QCheckBox("Dry-run con diff")
        self.cb_dry_improve.setChecked(True)
        i3.addWidget(self.cb_dry_improve)
        iv.addLayout(i3)
        v.addWidget(improve_box)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("Filtro análisis"))
        self.filter_edit = QLineEdit()
        filt.addWidget(self.filter_edit)
        v.addLayout(filt)
        self.analysis_table = QTableWidget(0, 6)
        self.analysis_table.setHorizontalHeaderLabels(["Repo", "Lenguajes", "Tecnologías", "Ficheros", "Docker", "Tests"])
        v.addWidget(self.analysis_table)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log)

        self.tpl_readme = ""
        self.tpl_license = ""
        self.tpl_codeowners = ""
        self.tpl_editorconfig = ""

        self.btn_status.clicked.connect(self.load_status)
        self.btn_apply.clicked.connect(self.apply_actions)
        self.btn_export_json.clicked.connect(lambda: self.export_rows("json"))
        self.btn_export_csv.clicked.connect(lambda: self.export_rows("csv"))
        self.btn_export_html.clicked.connect(lambda: self.export_rows("html"))
        self.btn_save_log.clicked.connect(self.save_log)
        self.btn_fixtures.clicked.connect(self.pick_fixtures_dir)
        self.btn_config.clicked.connect(self.pick_config)
        self.btn_tpl_readme.clicked.connect(lambda: self.pick_tpl("readme"))
        self.btn_readme_wizard.clicked.connect(self.open_readme_wizard)
        self.btn_tpl_license.clicked.connect(lambda: self.pick_tpl("license"))
        self.btn_tpl_codeowners.clicked.connect(lambda: self.pick_tpl("codeowners"))
        self.btn_tpl_editorconfig.clicked.connect(lambda: self.pick_tpl("editorconfig"))
        self.btn_analyze.clicked.connect(self.run_analyze)
        self.btn_improve.clicked.connect(self.run_improve)
        self.filter_edit.textChanged.connect(self.apply_filter)

        self.rows: List[Dict] = []

    def log_msg(self, s: str):
        self.log.append(s)

    def pick_tpl(self, kind: str):
        p, _ = QFileDialog.getOpenFileName(self, "Selecciona plantilla", "", "Todos (*)")
        if p:
            if kind == "readme":
                self.tpl_readme = p
            if kind == "license":
                self.tpl_license = p
            if kind == "codeowners":
                self.tpl_codeowners = p
            if kind == "editorconfig":
                self.tpl_editorconfig = p
            self.log_msg(f"Plantilla {kind}: {p}")

    def open_readme_wizard(self):
        owner = self.owner.text().strip()
        selected = self.selected_repo_names()
        repo = selected[0] if len(selected) == 1 else ""
        dlg = ReadmeWizard(owner, repo, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved_path:
            self.tpl_readme = dlg.saved_path
            self.log_msg(f"Plantilla readme (wizard): {dlg.saved_path}")

    def pick_fixtures_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Selecciona carpeta de fixtures", self.fixtures_dir.text().strip() or "")
        if p:
            self.fixtures_dir.setText(p)
            self.log_msg(f"Fixtures: {p}")

    def pick_config(self):
        p, _ = QFileDialog.getOpenFileName(self, "Selecciona config.json", "", "JSON (*.json);;Todos (*)")
        if p:
            self.config.setText(p)
            self.log_msg(f"Config: {p}")
            try:
                cfg = json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception:
                return
            defaults = cfg.get("defaults") if isinstance(cfg, dict) else {}
            if not isinstance(defaults, dict):
                return
            tb = None
            if isinstance(defaults.get("branch_objetivo"), str) and defaults.get("branch_objetivo").strip():
                tb = defaults.get("branch_objetivo").strip()
            if isinstance(defaults.get("target_branch"), str) and defaults.get("target_branch").strip():
                tb = defaults.get("target_branch").strip()
            if tb:
                self.target_branch.setText(tb)
            if isinstance(defaults.get("crear_readme"), bool):
                self.cb_readme.setChecked(defaults["crear_readme"])
            if isinstance(defaults.get("crear_license"), str):
                self.cb_license.setChecked(bool(defaults["crear_license"]))
            if isinstance(defaults.get("asegurar_workflows"), bool):
                self.cb_workflows.setChecked(defaults["asegurar_workflows"])
            if isinstance(defaults.get("habilitar_issues"), bool):
                self.cb_issues.setChecked(defaults["habilitar_issues"])
            if isinstance(defaults.get("crear_codeowners"), bool):
                self.cb_codeowners.setChecked(defaults["crear_codeowners"])
            if isinstance(defaults.get("crear_editorconfig"), bool):
                self.cb_editorconfig.setChecked(defaults["crear_editorconfig"])
            if isinstance(defaults.get("proteger_branch"), bool):
                self.cb_protect.setChecked(defaults["proteger_branch"])
            if isinstance(defaults.get("renombrar_branch"), bool):
                self.cb_rename.setChecked(defaults["renombrar_branch"])
            if isinstance(defaults.get("asegurar_main"), bool):
                self.cb_main.setChecked(defaults["asegurar_main"])
            if isinstance(defaults.get("default_main"), bool):
                self.cb_default_main.setChecked(defaults["default_main"])
            if isinstance(defaults.get("auto_topics"), bool):
                self.cb_auto_topics.setChecked(defaults["auto_topics"])
            if isinstance(defaults.get("auto_description"), bool):
                self.cb_auto_desc.setChecked(defaults["auto_description"])
            if isinstance(defaults.get("gitignore_auto"), bool):
                self.cb_gitignore.setChecked(defaults["gitignore_auto"])
            if isinstance(defaults.get("issues_templates"), bool):
                self.cb_issue_tpl.setChecked(defaults["issues_templates"])
            if isinstance(defaults.get("pr_template"), bool):
                self.cb_pr_tpl.setChecked(defaults["pr_template"])
            if isinstance(defaults.get("generar_readme_auto"), bool):
                self.cb_readme_auto.setChecked(defaults["generar_readme_auto"])
            if isinstance(defaults.get("forzar_readme"), bool):
                self.cb_readme_force.setChecked(defaults["forzar_readme"])
            if isinstance(defaults.get("recomendar_licencia"), bool):
                self.cb_lic_rec.setChecked(defaults["recomendar_licencia"])
            if isinstance(defaults.get("aplicar_licencia"), bool):
                self.cb_lic_apply.setChecked(defaults["aplicar_licencia"])
            if isinstance(defaults.get("workflows_ai"), bool):
                self.cb_wf_ai.setChecked(defaults["workflows_ai"])
            if isinstance(defaults.get("pages_static"), bool):
                self.cb_pages.setChecked(defaults["pages_static"])

    def load_status(self):
        token = self.token.text().strip() or None
        owner = self.owner.text().strip()
        if not owner:
            QMessageBox.warning(self, "Error", "Debes indicar owner")
            return
        params = {
            "token": token,
            "owner": owner,
            "owner_type": self.owner_type.currentText(),
            "visibility": self.visibility.currentText(),
            "concurrency": self.concurrency.value(),
            "target_branch": self.target_branch.text().strip() or "main",
            "offline": self.cb_offline.isChecked(),
            "fixtures_dir": self.fixtures_dir.text().strip() or "fixtures/sample",
            "config": self.config.text().strip() or None,
        }
        self.worker = Worker("status", params)
        self.worker.status_ready.connect(self.show_status)
        self.worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.worker.start()

    def show_status(self, rows: List[Dict]):
        self.rows = rows
        self.table.setRowCount(0)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(str(row.get("name", ""))))
            self.table.setItem(r, 2, QTableWidgetItem(str(row.get("private", ""))))
            self.table.setItem(r, 3, QTableWidgetItem(str(row.get("archived", ""))))
            self.table.setItem(r, 4, QTableWidgetItem(str(row.get("default_branch", ""))))
            self.table.setItem(r, 5, QTableWidgetItem(str(row.get("branch_name_ok", ""))))
            self.table.setItem(r, 6, QTableWidgetItem(str(row.get("branch_protected", ""))))
            self.table.setItem(r, 7, QTableWidgetItem(str(row.get("has_readme", ""))))
            self.table.setItem(r, 8, QTableWidgetItem(str(row.get("has_license", ""))))
            self.table.setItem(r, 9, QTableWidgetItem(str(row.get("has_workflows", ""))))
        self.log_msg(f"Repositorios: {len(rows)}")

    def selected_repo_names(self) -> List[str]:
        names = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                nm = self.table.item(r, 1).text()
                names.append(nm)
        return names

    def apply_actions(self):
        token = self.token.text().strip() or None
        owner = self.owner.text().strip()
        if not owner:
            QMessageBox.warning(self, "Error", "Debes indicar owner")
            return
        out_dir = Path("reportes")
        out_dir.mkdir(parents=True, exist_ok=True)
        audit_file = str(out_dir / f"audit_gui_{int(time.time())}.jsonl")
        params = {
            "token": token,
            "owner": owner,
            "owner_type": self.owner_type.currentText(),
            "visibility": self.visibility.currentText(),
            "concurrency": self.concurrency.value(),
            "target_branch": self.target_branch.text().strip() or "main",
            "audit_file": audit_file,
            "offline": self.cb_offline.isChecked(),
            "fixtures_dir": self.fixtures_dir.text().strip() or "fixtures/sample",
            "config": self.config.text().strip() or None,
            "selected_names": self.selected_repo_names(),
            "crear_readme": self.cb_readme.isChecked(),
            "crear_license": "mit" if self.cb_license.isChecked() else None,
            "asegurar_workflows": self.cb_workflows.isChecked(),
            "habilitar_issues": self.cb_issues.isChecked(),
            "crear_codeowners": self.cb_codeowners.isChecked(),
            "crear_editorconfig": self.cb_editorconfig.isChecked(),
            "proteger_branch": self.cb_protect.isChecked(),
            "renombrar_branch": self.cb_rename.isChecked(),
            "asegurar_main": self.cb_main.isChecked(),
            "default_main": self.cb_default_main.isChecked(),
            "dry_run": self.cb_dry.isChecked(),
            "crear_issue": self.cb_issue.isChecked(),
            "crear_pr": self.cb_pr.isChecked(),
            "branch": self.branch_pr.text().strip(),
            "tpl_readme": self.tpl_readme or None,
            "tpl_license": self.tpl_license or None,
            "tpl_codeowners": self.tpl_codeowners or None,
            "tpl_editorconfig": self.tpl_editorconfig or None,
        }
        self.worker = Worker("optimize", params)
        self.worker.result_ready.connect(self.show_results)
        self.worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.worker.start()

    def run_analyze(self):
        token = self.token.text().strip() or None
        owner = self.owner.text().strip()
        if not owner:
            QMessageBox.warning(self, "Error", "Debes indicar owner")
            return
        params = {
            "token": token,
            "owner": owner,
            "owner_type": self.owner_type.currentText(),
            "visibility": self.visibility.currentText(),
            "concurrency": self.concurrency.value(),
            "selected_names": self.selected_repo_names(),
            "offline": self.cb_offline.isChecked(),
            "fixtures_dir": self.fixtures_dir.text().strip() or "fixtures/sample",
            "config": self.config.text().strip() or None,
        }
        self.worker = Worker("analyze", params)
        self.worker.result_ready.connect(self.show_analysis)
        self.worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.worker.start()

    def run_improve(self):
        token = self.token.text().strip() or None
        owner = self.owner.text().strip()
        if not owner:
            QMessageBox.warning(self, "Error", "Debes indicar owner")
            return
        out_dir = Path("reportes")
        out_dir.mkdir(parents=True, exist_ok=True)
        audit_file = str(out_dir / f"audit_gui_{int(time.time())}.jsonl")
        params = {
            "token": token,
            "owner": owner,
            "owner_type": self.owner_type.currentText(),
            "visibility": self.visibility.currentText(),
            "concurrency": self.concurrency.value(),
            "audit_file": audit_file,
            "selected_names": self.selected_repo_names(),
            "offline": self.cb_offline.isChecked(),
            "fixtures_dir": self.fixtures_dir.text().strip() or "fixtures/sample",
            "config": self.config.text().strip() or None,
            "auto_topics": self.cb_auto_topics.isChecked(),
            "auto_description": self.cb_auto_desc.isChecked(),
            "gitignore_auto": self.cb_gitignore.isChecked(),
            "issues_templates": self.cb_issue_tpl.isChecked(),
            "pr_template": self.cb_pr_tpl.isChecked(),
            "generar_readme_auto": self.cb_readme_auto.isChecked(),
            "forzar_readme": self.cb_readme_force.isChecked(),
            "recomendar_licencia": self.cb_lic_rec.isChecked(),
            "aplicar_licencia": self.cb_lic_apply.isChecked(),
            "workflows_ai": self.cb_wf_ai.isChecked(),
            "pages_static": self.cb_pages.isChecked(),
            "dry_run": self.cb_dry_improve.isChecked(),
        }
        self.worker = Worker("improve", params)
        self.worker.result_ready.connect(self.show_results)
        self.worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.worker.start()

    def show_results(self, rows: List[Dict]):
        if not rows:
            self.log_msg("Sin resultados")
            return
        if isinstance(rows[0].get("actions", None), list):
            self.log_msg("Plan generado")
            for r in rows:
                self.log_msg(f"{r['name']}: {r['actions']}")
                if r.get("diffs"):
                    for d in r.get("diffs", []):
                        diff_text = d.get("diff") or ""
                        if diff_text.strip():
                            self.log_msg(f"{r['name']} {d.get('path','')}: {d.get('message','')}")
                            self.log_msg(diff_text)
        else:
            self.log_msg("Acciones ejecutadas")
            for r in rows:
                self.log_msg(f"{r['name']}: {r.get('executed', [])}")
        self.rows = rows

    def show_analysis(self, rows: List[Dict]):
        self.rows = rows
        self.populate_analysis_table(rows)

    def populate_analysis_table(self, rows: List[Dict]):
        self.analysis_table.setRowCount(0)
        for r in rows:
            name = r.get("name", "")
            langs = ",".join(sorted((r.get("languages") or {}).keys()))
            techs = ",".join(r.get("techs", []))
            files = str(r.get("files_count"))
            docker = str(r.get("has_dockerfile"))
            tests = str(r.get("has_tests"))
            rr = self.analysis_table.rowCount()
            self.analysis_table.insertRow(rr)
            self.analysis_table.setItem(rr, 0, QTableWidgetItem(name))
            self.analysis_table.setItem(rr, 1, QTableWidgetItem(langs))
            self.analysis_table.setItem(rr, 2, QTableWidgetItem(techs))
            self.analysis_table.setItem(rr, 3, QTableWidgetItem(files))
            self.analysis_table.setItem(rr, 4, QTableWidgetItem(docker))
            self.analysis_table.setItem(rr, 5, QTableWidgetItem(tests))

    def apply_filter(self, text: str):
        text = text.lower().strip()
        if not self.rows:
            return
        if not text:
            self.populate_analysis_table(self.rows)
            return
        filt = []
        for r in self.rows:
            s = " ".join([
                r.get("name", ""),
                ",".join(sorted((r.get("languages") or {}).keys())),
                ",".join(r.get("techs", [])),
            ]).lower()
            if text in s:
                filt.append(r)
        self.populate_analysis_table(filt)

    def export_rows(self, kind: str):
        if not self.rows:
            QMessageBox.information(self, "Info", "Primero carga el estado")
            return
        ts = int(time.time())
        out_dir = Path("reportes")
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"estado_gui_{ts}.{kind}"
        if kind == "json":
            write_json(self.rows, p)
        elif kind == "csv":
            write_csv(self.rows, p)
        elif kind == "html":
            write_html(self.rows, p, title="Estado repos")
        self.log_msg(f"Exportado: {p}")

    def save_log(self):
        ts = int(time.time())
        p, _ = QFileDialog.getSaveFileName(self, "Guardar log", f"log_{ts}.txt", "Texto (*.txt);;Todos (*)")
        if p:
            Path(p).write_text(self.log.toPlainText(), encoding="utf-8")
            self.log_msg(f"Log guardado: {p}")


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(1200, 700)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
