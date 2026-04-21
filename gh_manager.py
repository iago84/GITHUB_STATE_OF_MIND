import argparse
import base64
import csv
import datetime
import difflib
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    lvl = getattr(logging, (level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(lvl)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        root.addHandler(h)
    if log_file:
        fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    return logging.getLogger("gh_manager")


class AuditLogger:
    def __init__(self, path: Optional[str]):
        self.path = path
        self._fp = open(path, "a", encoding="utf-8") if path else None

    def emit(self, event: Dict) -> None:
        if not self._fp:
            return
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = dict(event)
        payload.setdefault("ts", ts)
        self._fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._fp.flush()

    def close(self) -> None:
        if self._fp:
            self._fp.close()
            self._fp = None


def _fixture_filename(method: str, url: str) -> str:
    u = urllib.parse.urlparse(url)
    path = u.path.lstrip("/")
    parts = [p for p in path.split("/") if p]
    base = "__".join(parts) if parts else "root"
    name = f"{method.lower()}__{base}"
    if u.query:
        q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        q_norm = "__".join([f"{k}-{v}" for k, v in q])
        if q_norm:
            name = f"{name}__{q_norm}"
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
    return f"{name}.json"


def unified_diff_text(old_text: str, new_text: str, path: str) -> str:
    return "\n".join(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))


def load_config(path: Optional[str]) -> Dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    return data


def _argv_has(argv: List[str], flag: str) -> bool:
    return flag in argv


def apply_config_defaults(args: argparse.Namespace, argv: List[str], cfg: Dict) -> argparse.Namespace:
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    if not isinstance(defaults, dict):
        defaults = {}
    flag_map = {
        "branch_objetivo": "--branch-objetivo",
        "visibility": "--visibility",
        "concurrency": "--concurrency",
        "crear_readme": "--crear-readme",
        "crear_license": "--crear-license",
        "asegurar_workflows": "--asegurar-workflows",
        "habilitar_issues": "--habilitar-issues",
        "crear_codeowners": "--crear-codeowners",
        "crear_editorconfig": "--crear-editorconfig",
        "proteger_branch": "--proteger-branch",
        "renombrar_branch": "--renombrar-branch",
        "asegurar_main": "--asegurar-main",
        "default_main": "--default-main",
        "auto_topics": "--auto-topics",
        "auto_description": "--auto-description",
        "gitignore_auto": "--gitignore-auto",
        "issues_templates": "--issues-templates",
        "pr_template": "--pr-template",
        "generar_readme_auto": "--generar-readme-auto",
        "forzar_readme": "--forzar-readme",
        "recomendar_licencia": "--recomendar-licencia",
        "aplicar_licencia": "--aplicar-licencia",
        "workflows_ai": "--workflows-ai",
        "pages_static": "--pages-static",
        "pages_root": "--pages-root",
        "pages_template": "--pages-template",
    }
    for key, val in defaults.items():
        if not hasattr(args, key):
            continue
        flag = flag_map.get(key)
        if flag and _argv_has(argv, flag):
            continue
        setattr(args, key, val)
    return args


class GitHubClient:
    def __init__(self, token: Optional[str] = None, user_agent: str = "gh-manager/1.0", audit: Optional[AuditLogger] = None):
        self.base = "https://api.github.com"
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": user_agent}
        self.audit = audit
        self.log = logging.getLogger("gh_manager.github")
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def _request(self, method: str, url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
        self.log.debug("%s %s", method, url)
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in self.headers.items():
            req.add_header(k, v)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = resp.getcode()
                body = resp.read()
                self.log.debug("status=%s url=%s", status, url)
                return status, dict(resp.headers), body
        except urllib.error.HTTPError as e:
            self.log.debug("status=%s url=%s", e.code, url)
            return e.code, dict(e.headers), e.read() if e.fp else b""
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error: {e}") from e

    def _paginate(self, url: str) -> List[Dict]:
        items: List[Dict] = []
        next_url = url
        while next_url:
            status, headers, body = self._request("GET", next_url)
            if status >= 400:
                raise RuntimeError(f"GitHub API error {status}: {body.decode(errors='ignore')}")
            items.extend(json.loads(body.decode()))
            link = headers.get("Link", "")
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            next_url = m.group(1) if m else None
            if next_url and not next_url.startswith("http"):
                next_url = urllib.parse.urljoin(self.base, next_url)
        return items

    def list_repos(self, owner: str, owner_type: str = "user", visibility: str = "all", archived: Optional[bool] = None) -> List[Dict]:
        if owner_type == "org":
            url = f"{self.base}/orgs/{owner}/repos?per_page=100&type=all"
        else:
            url = f"{self.base}/users/{owner}/repos?per_page=100&type=all"
        repos = self._paginate(url)
        filtered = []
        for r in repos:
            if visibility != "all" and r.get("visibility") != visibility:
                continue
            if archived is not None and bool(r.get("archived")) != archived:
                continue
            filtered.append(r)
        return filtered

    def repo(self, owner: str, name: str) -> Dict:
        status, _, body = self._request("GET", f"{self.base}/repos/{owner}/{name}")
        if status == 200:
            return json.loads(body.decode())
        raise RuntimeError(f"Unable to fetch repo {owner}/{name}: {status}")

    def has_readme(self, owner: str, name: str) -> bool:
        status, _, _ = self._request("GET", f"{self.base}/repos/{owner}/{name}/readme")
        return status == 200

    def has_license(self, owner: str, name: str) -> bool:
        status, _, _ = self._request("GET", f"{self.base}/repos/{owner}/{name}/license")
        return status == 200

    def has_workflows(self, owner: str, name: str) -> bool:
        status, _, body = self._request("GET", f"{self.base}/repos/{owner}/{name}/contents/.github/workflows")
        if status == 200:
            try:
                items = json.loads(body.decode())
                return isinstance(items, list) and len(items) > 0
            except Exception:
                return False
        return False

    def has_file(self, owner: str, name: str, path: str, ref: Optional[str] = None) -> bool:
        url = f"{self.base}/repos/{owner}/{name}/contents/{urllib.parse.quote(path)}"
        if ref:
            url += f"?ref={urllib.parse.quote(ref)}"
        status, _, _ = self._request("GET", url)
        return status == 200

    def has_codeowners(self, owner: str, name: str, ref: Optional[str] = None) -> bool:
        candidates = [
            "CODEOWNERS",
            ".github/CODEOWNERS",
            "docs/CODEOWNERS",
        ]
        for p in candidates:
            if self.has_file(owner, name, p, ref):
                return True
        return False

    def has_editorconfig(self, owner: str, name: str, ref: Optional[str] = None) -> bool:
        return self.has_file(owner, name, ".editorconfig", ref)

    def get_languages(self, owner: str, name: str) -> Dict[str, int]:
        status, _, body = self._request("GET", f"{self.base}/repos/{owner}/{name}/languages")
        if status == 200:
            try:
                return json.loads(body.decode())
            except Exception:
                return {}
        return {}

    def get_topics(self, owner: str, name: str) -> List[str]:
        status, _, body = self._request("GET", f"{self.base}/repos/{owner}/{name}/topics")
        if status == 200:
            try:
                return json.loads(body.decode()).get("names", [])
            except Exception:
                return []
        return []

    def set_topics(self, owner: str, name: str, topics: List[str]) -> bool:
        payload = {"names": topics}
        status, _, _ = self._request("PUT", f"{self.base}/repos/{owner}/{name}/topics", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if 200 <= status < 300 and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "set_topics", "ok": True, "topics": topics})
        return 200 <= status < 300

    def update_repo_description(self, owner: str, name: str, description: Optional[str] = None, homepage: Optional[str] = None) -> bool:
        payload: Dict[str, str] = {}
        if description is not None:
            payload["description"] = description
        if homepage is not None:
            payload["homepage"] = homepage
        if not payload:
            return True
        status, _, _ = self._request("PATCH", f"{self.base}/repos/{owner}/{name}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if 200 <= status < 300 and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "update_repo_description", "ok": True, "payload": payload})
        return 200 <= status < 300

    def get_tree(self, owner: str, name: str, branch: str, recursive: bool = True) -> Dict:
        sha = self.get_branch_sha(owner, name, branch)
        if not sha:
            return {}
        url = f"{self.base}/repos/{owner}/{name}/git/trees/{urllib.parse.quote(sha)}"
        if recursive:
            url += "?recursive=1"
        status, _, body = self._request("GET", url)
        if status == 200:
            try:
                return json.loads(body.decode())
            except Exception:
                return {}
        return {}

    def get_file_text(self, owner: str, name: str, path: str, ref: Optional[str] = None) -> Optional[str]:
        url = f"{self.base}/repos/{owner}/{name}/contents/{urllib.parse.quote(path)}"
        if ref:
            url += f"?ref={urllib.parse.quote(ref)}"
        status, _, body = self._request("GET", url)
        if status == 200:
            try:
                j = json.loads(body.decode())
                content_b64 = j.get("content", "")
                return base64.b64decode(content_b64).decode("utf-8", errors="ignore")
            except Exception:
                return None
        return None

    def download_zip(self, owner: str, name: str, ref: Optional[str], dest_dir: Path) -> Path:
        if not ref:
            info = self.repo(owner, name)
            ref = info.get("default_branch", "main")
        url = f"{self.base}/repos/{owner}/{name}/zipball/{urllib.parse.quote(ref)}"
        status, headers, body = self._request("GET", url, headers={"Accept": "application/vnd.github+json"})
        if status >= 400:
            raise RuntimeError(f"Download failed for {owner}/{name}@{ref}: {status}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = dest_dir / f"{name}-{ref}.zip"
        with open(fname, "wb") as f:
            f.write(body)
        return fname

    def enable_issues(self, owner: str, name: str) -> bool:
        payload = {"has_issues": True}
        status, _, _ = self._request("PATCH", f"{self.base}/repos/{owner}/{name}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if status in (200, 201) and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "enable_issues", "ok": True})
        return status in (200, 201)

    def set_default_branch(self, owner: str, name: str, branch: str) -> bool:
        status, _, _ = self._request("PATCH", f"{self.base}/repos/{owner}/{name}", data=json.dumps({"default_branch": branch}).encode(), headers={"Content-Type": "application/json"})
        if status in (200, 201) and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "set_default_branch", "ok": True, "branch": branch})
        return status in (200, 201)

    def branch_protection_exists(self, owner: str, name: str, branch: str) -> bool:
        status, _, _ = self._request("GET", f"{self.base}/repos/{owner}/{name}/branches/{urllib.parse.quote(branch)}/protection")
        return status == 200

    def protect_branch(self, owner: str, name: str, branch: str, admins: bool = True, reviews: int = 1) -> bool:
        url = f"{self.base}/repos/{owner}/{name}/branches/{urllib.parse.quote(branch)}/protection"
        payload = {
            "required_status_checks": {"strict": True, "contexts": []},
            "enforce_admins": admins,
            "required_pull_request_reviews": {"required_approving_review_count": reviews},
            "restrictions": None,
        }
        status, _, _ = self._request("PUT", url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if 200 <= status < 300 and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "protect_branch", "ok": True, "branch": branch, "admins": admins, "reviews": reviews})
        return 200 <= status < 300

    def get_branch_sha(self, owner: str, name: str, branch: str) -> Optional[str]:
        status, _, body = self._request("GET", f"{self.base}/repos/{owner}/{name}/git/ref/heads/{urllib.parse.quote(branch)}")
        if status == 200:
            try:
                return json.loads(body.decode()).get("object", {}).get("sha")
            except Exception:
                return None
        return None

    def create_branch(self, owner: str, name: str, new_branch: str, base_branch: str) -> bool:
        sha = self.get_branch_sha(owner, name, base_branch)
        if not sha:
            return False
        payload = {"ref": f"refs/heads/{new_branch}", "sha": sha}
        status, _, _ = self._request("POST", f"{self.base}/repos/{owner}/{name}/git/refs", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if status in (200, 201) and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "create_branch", "ok": True, "new_branch": new_branch, "base_branch": base_branch})
        return status in (201, 200)

    def rename_branch(self, owner: str, name: str, old: str, new: str) -> bool:
        status, _, _ = self._request("POST", f"{self.base}/repos/{owner}/{name}/branches/{urllib.parse.quote(old)}/rename", data=json.dumps({"new_name": new}).encode(), headers={"Content-Type": "application/json"})
        if 200 <= status < 300 and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "rename_branch", "ok": True, "old": old, "new": new})
        return 200 <= status < 300

    def create_issue(self, owner: str, name: str, title: str, body: str) -> bool:
        status, _, _ = self._request("POST", f"{self.base}/repos/{owner}/{name}/issues", data=json.dumps({"title": title, "body": body}).encode(), headers={"Content-Type": "application/json"})
        if status in (200, 201) and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "create_issue", "ok": True, "title": title})
        return status in (201, 200)

    def create_pull_request(self, owner: str, name: str, title: str, body: str, head: str, base: str) -> bool:
        payload = {"title": title, "body": body, "head": head, "base": base}
        status, _, _ = self._request("POST", f"{self.base}/repos/{owner}/{name}/pulls", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if status in (200, 201) and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "create_pull_request", "ok": True, "title": title, "head": head, "base": base})
        return status in (201, 200)

    def list_branches(self, owner: str, name: str) -> List[Dict]:
        url = f"{self.base}/repos/{owner}/{name}/branches?per_page=100"
        return self._paginate(url)

    def get_commit_date(self, owner: str, name: str, sha: str) -> Optional[str]:
        status, _, body = self._request("GET", f"{self.base}/repos/{owner}/{name}/commits/{urllib.parse.quote(sha)}")
        if status == 200:
            try:
                j = json.loads(body.decode())
                return j.get("commit", {}).get("author", {}).get("date")
            except Exception:
                return None
        return None

    def create_or_update_file(self, owner: str, name: str, path: str, content: str, message: str, branch: Optional[str] = None) -> bool:
        url = f"{self.base}/repos/{owner}/{name}/contents/{urllib.parse.quote(path)}"
        status, _, body = self._request("GET", url + (f"?ref={urllib.parse.quote(branch)}" if branch else ""))
        sha = None
        if status == 200:
            try:
                sha = json.loads(body.decode()).get("sha")
            except Exception:
                sha = None
        payload = {"message": message, "content": base64.b64encode(content.encode()).decode()}
        if branch:
            payload["branch"] = branch
        if sha:
            payload["sha"] = sha
        status2, _, _ = self._request("PUT", url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        if status2 in (200, 201) and self.audit:
            self.audit.emit({"owner": owner, "repo": name, "action": "create_or_update_file", "ok": True, "path": path, "branch": branch, "message": message})
        return status2 in (200, 201)


class FixtureGitHubClient(GitHubClient):
    def __init__(self, fixtures_dir: str, token: Optional[str] = None, user_agent: str = "gh-manager/1.0", audit: Optional[AuditLogger] = None):
        super().__init__(token=token, user_agent=user_agent, audit=audit)
        self.fixtures_dir = Path(fixtures_dir)

    def _request(self, method: str, url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
        fname = _fixture_filename(method, url)
        p = self.fixtures_dir / fname
        if method.upper() != "GET":
            return 501, {}, b""
        if not p.exists():
            return 404, {}, b""
        return 200, {}, p.read_bytes()


class RepoAnalyzer:
    def __init__(self, gh: GitHubClient, owner: str, target_branch_name: str = "main", concurrency: int = 4):
        self.gh = gh
        self.owner = owner
        self.target_branch_name = target_branch_name
        self.concurrency = max(1, concurrency)

    def _summ_one(self, r: Dict) -> Dict:
        name = r.get("name")
        try:
            has_readme = self.gh.has_readme(self.owner, name)
            has_license = self.gh.has_license(self.owner, name)
            has_workflows = self.gh.has_workflows(self.owner, name)
            default_branch = r.get("default_branch")
            protected = False
            try:
                if default_branch:
                    protected = self.gh.branch_protection_exists(self.owner, name, default_branch)
            except Exception:
                protected = False
            has_codeowners = self.gh.has_codeowners(self.owner, name, default_branch)
            has_editorconfig = self.gh.has_editorconfig(self.owner, name, default_branch)
            issues_enabled = bool(r.get("has_issues"))
            open_issues = int(r.get("open_issues_count", 0))
            archived = bool(r.get("archived"))
            return {
                "name": name,
                "private": bool(r.get("private")),
                "archived": archived,
                "default_branch": default_branch,
                "branch_name_ok": default_branch == self.target_branch_name,
                "branch_protected": protected,
                "has_readme": has_readme,
                "has_license": has_license,
                "has_workflows": has_workflows,
                "has_codeowners": has_codeowners,
                "has_editorconfig": has_editorconfig,
                "issues_enabled": issues_enabled,
                "open_issues": open_issues,
            }
        except Exception as e:
            return {"name": name, "error": str(e)}

    def summarize(self, repos: List[Dict]) -> List[Dict]:
        rows: List[Dict] = []
        if self.concurrency > 1:
            with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
                futs = [ex.submit(self._summ_one, r) for r in repos]
                for fut in as_completed(futs):
                    rows.append(fut.result())
        else:
            for r in repos:
                rows.append(self._summ_one(r))
        return rows


class RepoDownloader:
    def __init__(self, gh: GitHubClient, owner: str, dest: Path):
        self.gh = gh
        self.owner = owner
        self.dest = dest

    def download(self, repos: List[str], ref: Optional[str] = None) -> List[Tuple[str, Path]]:
        results: List[Tuple[str, Path]] = []
        for name in repos:
            p = self.gh.download_zip(self.owner, name, ref, self.dest)
            results.append((name, p))
            time.sleep(0.1)
        return results


class DeepAnalyzer:
    def __init__(self, gh: GitHubClient, owner: str):
        self.gh = gh
        self.owner = owner

    def analyze(self, repo: Dict) -> Dict:
        name = repo.get("name")
        default_branch = repo.get("default_branch") or "main"
        languages = self.gh.get_languages(self.owner, name)
        topics = self.gh.get_topics(self.owner, name)
        tree = self.gh.get_tree(self.owner, name, default_branch, recursive=True).get("tree", [])
        paths = [t.get("path") for t in tree if t.get("type") == "blob"]
        has_docker = any(p.lower().endswith("dockerfile") or "/dockerfile" in p.lower() for p in paths if isinstance(p, str))
        has_tests = any(p.lower().startswith(("tests/", "test/")) for p in paths if isinstance(p, str))
        pkg_json_count = sum(1 for p in paths if isinstance(p, str) and p.endswith("package.json"))
        go_mod_count = sum(1 for p in paths if isinstance(p, str) and p.endswith("go.mod"))
        java_build_count = sum(1 for p in paths if isinstance(p, str) and (p.endswith("pom.xml") or p.endswith("build.gradle") or p.endswith("build.gradle.kts")))
        deps: Dict[str, List[str]] = {}
        files_to_check = {
            "package.json": "node",
            "requirements.txt": "python",
            "pyproject.toml": "python",
            "go.mod": "go",
            "Cargo.toml": "rust",
            "pom.xml": "java",
            "build.gradle": "java",
            "Dockerfile": "docker",
        }
        rust_workspace = False
        for fn, kind in files_to_check.items():
            if any(p.endswith(fn) for p in paths):
                content = self.gh.get_file_text(self.owner, name, fn, ref=default_branch) or ""
                if kind == "node":
                    try:
                        j = json.loads(content)
                        deps["node"] = sorted(list((j.get("dependencies") or {}).keys()) + list((j.get("devDependencies") or {}).keys()))
                    except Exception:
                        deps["node"] = []
                elif kind == "python":
                    pkgs: List[str] = []
                    if fn == "requirements.txt":
                        pkgs = [line.split("==")[0].strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
                    else:
                        m = re.findall(r'name\s*=\s*["\']([^"\']+)["\']', content)
                        pkgs = m or []
                    deps["python"] = sorted(set(pkgs))
                elif kind == "go":
                    pkgs = [line.split(" ")[0] for line in content.splitlines() if line.strip().startswith(("require ", "require("))]
                    deps["go"] = pkgs
                elif kind == "rust":
                    if "[workspace]" in content:
                        rust_workspace = True
                    pkgs = [m.group(1) for m in re.finditer(r'^\s*([A-Za-z0-9_\-]+)\s*=\s*', content, re.M)]
                    deps["rust"] = pkgs
                elif kind == "java":
                    deps["java"] = ["java-deps"]
                elif kind == "docker":
                    deps["docker"] = ["docker"]
        techs = self._infer_techs(languages, deps, paths)
        return {
            "name": name,
            "default_branch": default_branch,
            "languages": languages,
            "topics": topics,
            "dependencies": deps,
            "has_dockerfile": has_docker,
            "has_tests": has_tests,
            "techs": sorted(techs),
            "files_count": len(paths),
            "node_pkg_count": pkg_json_count,
            "node_monorepo": pkg_json_count > 1 or any(isinstance(p, str) and p.startswith("packages/") for p in paths),
            "go_multidir": go_mod_count > 1,
            "rust_workspace": rust_workspace,
            "java_multimodule": java_build_count > 1,
        }

    def _infer_techs(self, languages: Dict[str, int], deps: Dict[str, List[str]], paths: List[str]) -> List[str]:
        techs = set(k.lower() for k in languages.keys())
        node_deps = set(deps.get("node", []))
        py_deps = set(deps.get("python", []))
        lower_paths = [p.lower() for p in paths if isinstance(p, str)]
        if {"react", "react-dom"} & node_deps:
            techs.add("react")
        if "next" in node_deps:
            techs.add("nextjs")
        if "express" in node_deps:
            techs.add("express")
        if "flask" in py_deps:
            techs.add("flask")
        if "django" in py_deps:
            techs.add("django")
        if any(p.endswith(".ipynb") for p in lower_paths):
            techs.add("jupyter")
        if any(p.endswith(".tf") for p in lower_paths):
            techs.add("terraform")
        helm_signals = ("chart.yaml", "values.yaml")
        if any(p.endswith(helm_signals) or p.startswith(("helm/", "charts/", "chart/")) or "/charts/" in p for p in lower_paths):
            techs.add("helm")
        if any(p.startswith(("k8s/", "kubernetes/")) or "/k8s/" in p or "/kubernetes/" in p for p in lower_paths):
            techs.add("kubernetes")
        monorepo_signals = ("pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json")
        pkg_json_count = sum(1 for p in lower_paths if p.endswith("package.json"))
        if pkg_json_count > 1 or any(p.startswith("packages/") for p in lower_paths) or any(p.endswith(monorepo_signals) for p in lower_paths):
            techs.add("monorepo")
        return list(techs)


class LicenseAdvisor:
    def recommend(self, repo: Dict, analysis: Dict) -> str:
        if repo.get("private"):
            return "proprietary"
        langs = set((analysis.get("languages") or {}).keys())
        if "Java" in langs:
            return "apache-2.0"
        if "C" in langs or "C++" in langs:
            return "gpl-3.0"
        return "mit"


class ReadmeGenerator:
    def __init__(self, owner: str):
        self.owner = owner

    def build(self, repo: Dict, analysis: Dict) -> str:
        name = repo.get("name")
        desc = repo.get("description") or "Descripción del proyecto."
        badge_base = f"https://img.shields.io"
        license_badge = f"{badge_base}/badge/license-unknown-lightgray"
        if repo.get('license') and repo['license'].get('spdx_id'):
            spdx = repo['license']['spdx_id']
            license_badge = f"{badge_base}/badge/license-{spdx}-blue"
        ci_badge = f"{badge_base}/github/actions/workflow/status/{self.owner}/{name}/ci.yml?branch={analysis.get('default_branch','main')}"
        issues_badge = f"{badge_base}/github/issues/{self.owner}/{name}"
        langs = ", ".join(sorted((analysis.get("languages") or {}).keys()))
        install_section = self._install_section(analysis)
        usage_section = self._usage_section(analysis)
        return (
            f"# {name}\n\n"
            f"{desc}\n\n"
            f"![License]({license_badge}) ![CI]({ci_badge}) ![Issues]({issues_badge})\n\n"
            f"## Tecnologías\n\n{langs or 'N/A'}\n\n"
            f"## Instalación\n\n{install_section}\n\n"
            f"## Uso\n\n{usage_section}\n\n"
            f"## Contribución\n\n"
            f"Por favor abre un issue o PR. Revisa CODEOWNERS y la plantilla de PR.\n\n"
            f"## Licencia\n\nConsulta el archivo LICENSE.\n"
        )

    def _install_section(self, analysis: Dict) -> str:
        techs = set(analysis.get("techs", []))
        if "python" in techs or "Python" in analysis.get("languages", {}):
            return "- Crea un entorno virtual\n- pip install -r requirements.txt"
        if "javascript" in techs or "TypeScript" in analysis.get("languages", {}) or "JavaScript" in analysis.get("languages", {}):
            return "- npm install"
        return "Pasos de instalación pendientes de definir."

    def _usage_section(self, analysis: Dict) -> str:
        if "flask" in analysis.get("techs", []) or "django" in analysis.get("techs", []):
            return "Inicia el servidor y visita http://localhost:8000"
        if "react" in analysis.get("techs", []) or "nextjs" in analysis.get("techs", []):
            return "npm run dev y abre http://localhost:3000"
        return "Descripción de uso pendiente de definir."

class Optimizer:
    def __init__(self, gh: GitHubClient, owner: str, target_branch_name: str = "main", templates: Optional[Dict[str, str]] = None):
        self.gh = gh
        self.owner = owner
        self.target_branch_name = target_branch_name
        self.templates = templates or {}

    def plan(self, status_rows: List[Dict], create_readme: bool, create_license: Optional[str], ensure_workflows: bool, enable_issues: bool, create_codeowners: bool = False, create_editorconfig: bool = False, protect_branch: bool = False, rename_branch: bool = False, ensure_main: bool = False, default_main: bool = False) -> List[Dict]:
        plans: List[Dict] = []
        for row in status_rows:
            if "error" in row:
                continue
            actions: List[str] = []
            if create_readme and not row.get("has_readme"):
                actions.append("create_readme")
            if create_license and not row.get("has_license"):
                actions.append(f"create_license:{create_license}")
            if ensure_workflows and not row.get("has_workflows"):
                actions.append("ensure_workflows")
            if enable_issues and not row.get("issues_enabled"):
                actions.append("enable_issues")
            if create_codeowners and not row.get("has_codeowners"):
                actions.append("create_codeowners")
            if create_editorconfig and not row.get("has_editorconfig"):
                actions.append("create_editorconfig")
            if protect_branch and not row.get("branch_protected") and row.get("default_branch"):
                actions.append("protect_branch")
            if rename_branch and not row.get("branch_name_ok") and row.get("default_branch"):
                actions.append(f"rename_branch:{row.get('default_branch')}->{self.target_branch_name}")
            if ensure_main:
                actions.append("ensure_main")
            if default_main:
                actions.append("set_default_main")
            if actions:
                plans.append({"name": row["name"], "actions": actions})
        return plans

    def preview(self, plans: List[Dict], default_branches: Dict[str, str]) -> List[Dict]:
        rows: List[Dict] = []
        for plan in plans:
            name = plan["name"]
            ref = default_branches.get(name) or self.target_branch_name
            diffs: List[Dict] = []
            for action in plan["actions"]:
                fc = self._file_change_for_action(name, action)
                if not fc:
                    diffs.append({"path": "", "message": action, "diff": ""})
                    continue
                path, message, content = fc
                old = self.gh.get_file_text(self.owner, name, path, ref=ref)
                old_text = old or ""
                diff_text = unified_diff_text(old_text, content, path)
                if old is None and not diff_text:
                    diff_text = f"CREATE {path}"
                diffs.append({"path": path, "message": message, "diff": diff_text})
            rows.append({"name": name, "actions": plan["actions"], "diffs": diffs})
        return rows

    def _file_change_for_action(self, repo: str, action: str) -> Optional[Tuple[str, str, str]]:
        y = time.gmtime().tm_year
        if action == "create_readme":
            content = self.templates.get("readme", f"# {repo}\n")
            content = content.replace("{{REPO_NAME}}", repo).replace("{{OWNER}}", self.owner).replace("{{YEAR}}", str(y))
            return "README.md", "Add README", content
        if action.startswith("create_license:"):
            spdx = action.split(":", 1)[1]
            text = self.templates.get("license", self._license_text(spdx, repo))
            text = text.replace("{{REPO_NAME}}", repo).replace("{{OWNER}}", self.owner).replace("{{YEAR}}", str(y))
            return "LICENSE", "Add LICENSE", text
        if action == "ensure_workflows":
            wf = self.templates.get("workflow_ci", "name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - name: Build\n        run: echo Build\n")
            return ".github/workflows/ci.yml", "Add basic CI workflow", wf
        if action == "create_codeowners":
            co = self.templates.get("codeowners", "* @%s" % self.owner)
            co = co.replace("OWNER_OR_TEAM", self.owner).replace("@@", "@")
            return ".github/CODEOWNERS", "Add CODEOWNERS", co
        if action == "create_editorconfig":
            ec = self.templates.get("editorconfig", "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\ncharset = utf-8\nindent_style = space\nindent_size = 2\n")
            return ".editorconfig", "Add .editorconfig", ec
        return None

    def execute(self, plans: List[Dict], branch: Optional[str] = None, open_pr: bool = False, base_branch: Optional[str] = None) -> List[Dict]:
        results: List[Dict] = []
        for plan in plans:
            name = plan["name"]
            executed: List[str] = []
            created_branch = False
            head_branch = branch
            if open_pr and branch and base_branch:
                if self.gh.create_branch(self.owner, name, branch, base_branch):
                    created_branch = True
                    head_branch = branch
            for action in plan["actions"]:
                if action == "create_readme":
                    content = self.templates.get("readme", f"# {name}\n")
                    y = time.gmtime().tm_year
                    content = content.replace("{{REPO_NAME}}", name).replace("{{OWNER}}", self.owner).replace("{{YEAR}}", str(y))
                    ok = self.gh.create_or_update_file(self.owner, name, "README.md", content, "Add README", head_branch)
                    if ok:
                        executed.append(action)
                elif action.startswith("create_license:"):
                    spdx = action.split(":", 1)[1]
                    text = self.templates.get("license", self._license_text(spdx, name))
                    y = time.gmtime().tm_year
                    text = text.replace("{{REPO_NAME}}", name).replace("{{OWNER}}", self.owner).replace("{{YEAR}}", str(y))
                    ok = self.gh.create_or_update_file(self.owner, name, "LICENSE", text, "Add LICENSE", head_branch)
                    if ok:
                        executed.append(action)
                elif action == "ensure_workflows":
                    wf = self.templates.get("workflow_ci", "name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - name: Build\n        run: echo Build\n")
                    ok = self.gh.create_or_update_file(self.owner, name, ".github/workflows/ci.yml", wf, "Add basic CI workflow", head_branch)
                    if ok:
                        executed.append(action)
                elif action == "enable_issues":
                    if self.gh.enable_issues(self.owner, name):
                        executed.append(action)
                elif action == "create_codeowners":
                    co = self.templates.get("codeowners", "* @%s" % self.owner)
                    co = co.replace("OWNER_OR_TEAM", self.owner).replace("@@", "@")
                    path = ".github/CODEOWNERS"
                    ok = self.gh.create_or_update_file(self.owner, name, path, co, "Add CODEOWNERS", head_branch)
                    if ok:
                        executed.append(action)
                elif action == "create_editorconfig":
                    ec = self.templates.get("editorconfig", "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\ncharset = utf-8\nindent_style = space\nindent_size = 2\n")
                    ok = self.gh.create_or_update_file(self.owner, name, ".editorconfig", ec, "Add .editorconfig", head_branch)
                    if ok:
                        executed.append(action)
                elif action == "protect_branch":
                    base = base_branch or self.target_branch_name
                    if self.gh.protect_branch(self.owner, name, base):
                        executed.append(action)
                elif action.startswith("rename_branch:"):
                    seg = action.split(":", 1)[1]
                    old, new = seg.split("->")
                    if self.gh.rename_branch(self.owner, name, old, new):
                        executed.append(action)
                elif action == "ensure_main":
                    branches = self.gh.list_branches(self.owner, name)
                    names = [b.get("name") for b in branches]
                    if "main" not in names:
                        latest = None
                        latest_date = ""
                        for b in branches:
                            sha = b.get("commit", {}).get("sha")
                            d = self.gh.get_commit_date(self.owner, name, sha) or ""
                            if d > latest_date:
                                latest_date = d
                                latest = b.get("name")
                        if latest:
                            if self.gh.create_branch(self.owner, name, "main", latest):
                                executed.append(f"create_branch:main_from:{latest}")
                elif action == "set_default_main":
                    if self.gh.set_default_branch(self.owner, name, "main"):
                        executed.append(action)
            if open_pr and branch and base_branch:
                if created_branch:
                    title = "Housekeeping"
                    body = "Automated housekeeping changes"
                    if self.gh.create_pull_request(self.owner, name, title, body, head=branch, base=base_branch):
                        executed.append("open_pr")
            results.append({"name": name, "executed": executed})
        return results

    def _license_text(self, spdx: str, project: str) -> str:
        y = time.gmtime().tm_year
        if spdx.lower() in ("mit", "mit-license", "mit_license"):
            return f"MIT License\n\nCopyright (c) {y} {self.owner}\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the \"Software\"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n"
        if spdx.lower() in ("apache-2.0", "apache2", "apache"):
            return f"Apache License 2.0 placeholder for {project}\nSee https://www.apache.org/licenses/LICENSE-2.0"
        if spdx.lower() in ("gpl-3.0", "gpl3", "gpl"):
            return f"GPL-3.0 License placeholder for {project}\nSee https://www.gnu.org/licenses/gpl-3.0.en.html"
        return f"License for {project}"


def print_table(rows: List[Dict], columns: List[Tuple[str, str]]) -> None:
    widths = []
    for key, header in columns:
        width = max(len(header), *(len(str(row.get(key, ""))) for row in rows)) if rows else len(header)
        widths.append(width)
    header_line = " | ".join(h.ljust(w) for (_, h), w in zip(columns, widths))
    sep = "-+-".join("-" * w for w in widths)
    print(header_line)
    print(sep)
    for row in rows:
        print(" | ".join(str(row.get(key, "")).ljust(w) for (key, _), w in zip(columns, widths)))


def write_json(rows: List[Dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def write_csv(rows: List[Dict], out_path: Path, columns: Optional[List[str]] = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    cols = columns or sorted({k for r in rows for k in r.keys()})
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def write_html(rows: List[Dict], out_path: Path, title: str = "Reporte") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = sorted({k for r in rows for k in r.keys()})
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = "\n".join("<tr>" + "".join(f"<td>{str(r.get(c,''))}</td>" for c in cols) + "</tr>" for r in rows)
    html = f"<!doctype html><meta charset='utf-8'><title>{title}</title><style>table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px}}</style><h1>{title}</h1><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gh-manager", description="Gestor de repositorios de GitHub")
    p.add_argument("--token", help="Token de acceso a GitHub. Por defecto GITHUB_TOKEN", default=None)
    p.add_argument("--config", default=None, help="Ruta a config.json con defaults y convenciones")
    p.add_argument("--log-level", default="INFO", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)")
    p.add_argument("--log-file", default=None, help="Ruta a fichero de log")
    p.add_argument("--audit-file", default=None, help="Ruta a fichero JSONL de auditoría")
    p.add_argument("--offline", action="store_true", help="Ejecutar sin red usando fixtures locales")
    p.add_argument("--fixtures-dir", default="fixtures/sample", help="Directorio con fixtures JSON para --offline")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_owner_args(sp):
        g = sp.add_mutually_exclusive_group(required=True)
        g.add_argument("--user", help="Usuario propietario")
        g.add_argument("--org", help="Organización propietaria")
        sp.add_argument("--visibility", choices=["all", "public", "private"], default="all")
        sp.add_argument("--include-archived", action="store_true")
        sp.add_argument("--concurrency", type=int, default=4)
        sp.add_argument("--branch-objetivo", default="main")

    sp_list = sub.add_parser("listar", help="Listar repositorios")
    add_owner_args(sp_list)

    sp_status = sub.add_parser("estado", help="Mostrar estado de repositorios")
    add_owner_args(sp_status)
    sp_status.add_argument("--output", choices=["table", "json", "csv", "html"], default="table")
    sp_status.add_argument("--out-dir", default="reportes")

    sp_dl = sub.add_parser("descargar", help="Descargar repositorios como ZIP")
    add_owner_args(sp_dl)
    sp_dl.add_argument("--dest", default="downloads", help="Directorio de destino")
    sp_dl.add_argument("--ref", default=None, help="Rama o tag a descargar")
    sp_dl.add_argument("--repos", nargs="*", help="Lista de repos a descargar; si vacío, todos")

    sp_opt = sub.add_parser("optimizar", help="Rutina de optimización de repos")
    add_owner_args(sp_opt)
    sp_opt.add_argument("--repos", nargs="*", help="Repos a optimizar; si vacío, todos")
    sp_opt.add_argument("--dry-run", action="store_true", help="Solo mostrar acciones sin ejecutarlas")
    sp_opt.add_argument("--crear-readme", action="store_true", help="Crear README si falta")
    sp_opt.add_argument("--crear-license", choices=["mit", "apache-2.0", "gpl-3.0"], help="Crear LICENSE si falta")
    sp_opt.add_argument("--asegurar-workflows", action="store_true", help="Crear workflow básico si faltan")
    sp_opt.add_argument("--habilitar-issues", action="store_true", help="Habilitar Issues si están desactivados")
    sp_opt.add_argument("--branch", default=None, help="Rama donde escribir cambios")
    sp_opt.add_argument("--crear-codeowners", action="store_true", help="Crear CODEOWNERS si falta")
    sp_opt.add_argument("--crear-editorconfig", action="store_true", help="Crear .editorconfig si falta")
    sp_opt.add_argument("--proteger-branch", action="store_true", help="Proteger la rama objetivo")
    sp_opt.add_argument("--renombrar-branch", action="store_true", help="Renombrar rama por defecto a la rama objetivo")
    sp_opt.add_argument("--asegurar-main", action="store_true", help="Crear rama main desde la rama más actual si no existe")
    sp_opt.add_argument("--default-main", action="store_true", help="Establecer main como rama por defecto")
    sp_opt.add_argument("--crear-issue-housekeeping", action="store_true", help="Crear issue con las acciones propuestas")
    sp_opt.add_argument("--crear-pr-housekeeping", action="store_true", help="Crear PR con los cambios propuestos")
    sp_opt.add_argument("--plantilla-readme", help="Ruta a plantilla README.md")
    sp_opt.add_argument("--plantilla-license", help="Ruta a plantilla LICENSE")
    sp_opt.add_argument("--plantilla-codeowners", help="Ruta a plantilla CODEOWNERS")
    sp_opt.add_argument("--plantilla-editorconfig", help="Ruta a plantilla .editorconfig")
    sp_opt.add_argument("--output", choices=["table", "json", "csv", "html"], default="table")
    sp_opt.add_argument("--out-dir", default="reportes")

    sp_deep = sub.add_parser("analizar", help="Análisis profundo de contenido")
    add_owner_args(sp_deep)
    sp_deep.add_argument("--repos", nargs="*", help="Repos a analizar; si vacío, todos")
    sp_deep.add_argument("--output", choices=["table", "json", "csv", "html"], default="table")
    sp_deep.add_argument("--out-dir", default="reportes")

    sp_better = sub.add_parser("mejorar", help="Mejorar metadatos y configuración")
    add_owner_args(sp_better)
    sp_better.add_argument("--repos", nargs="*", help="Repos a mejorar; si vacío, todos")
    sp_better.add_argument("--auto-topics", action="store_true", help="Añadir topics según análisis")
    sp_better.add_argument("--auto-description", action="store_true", help="Completar descripción si falta")
    sp_better.add_argument("--gitignore-auto", action="store_true", help="Añadir .gitignore segun tecnología si falta")
    sp_better.add_argument("--issues-templates", action="store_true", help="Añadir plantillas de Issues si faltan")
    sp_better.add_argument("--pr-template", action="store_true", help="Añadir plantilla de PR si falta")
    sp_better.add_argument("--generar-readme-auto", action="store_true", help="Generar README personalizado si falta")
    sp_better.add_argument("--forzar-readme", action="store_true", help="Regenerar README aunque exista")
    sp_better.add_argument("--recomendar-licencia", action="store_true", help="Recomendar licencia")
    sp_better.add_argument("--aplicar-licencia", action="store_true", help="Aplicar licencia recomendada")
    sp_better.add_argument("--workflows-ai", action="store_true", help="Crear workflow CI según tecnología si falta")
    sp_better.add_argument("--pages-static", action="store_true", help="Configurar despliegue de sitio estático a GitHub Pages")
    sp_better.add_argument("--pages-root", default="public", help="Carpeta raíz para publicar en Pages")
    sp_better.add_argument("--pages-template", choices=["auto", "static", "docsify"], default="static", help="Plantilla de sitio para Pages")
    sp_better.add_argument("--dry-run", action="store_true", help="Previsualizar cambios con diffs sin aplicar")
    sp_better.add_argument("--output", choices=["table", "json", "csv", "html"], default="table")
    sp_better.add_argument("--out-dir", default="reportes")

    return p.parse_args(argv)


def resolve_owner(args: argparse.Namespace) -> Tuple[str, str]:
    owner = args.user or args.org
    owner_type = "org" if args.org else "user"
    return owner, owner_type


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    argv_list = argv if argv is not None else sys.argv[1:]
    cfg = load_config(getattr(args, "config", None))
    apply_config_defaults(args, argv_list, cfg)
    setup_logging(args.log_level, args.log_file)
    audit = AuditLogger(args.audit_file) if args.audit_file else None
    try:
        gh = FixtureGitHubClient(args.fixtures_dir, token=args.token, audit=audit) if args.offline else GitHubClient(token=args.token, audit=audit)
        owner, owner_type = resolve_owner(args)
        archived = None if not getattr(args, "include_archived", False) else None
        if args.cmd == "listar":
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=args.visibility, archived=archived)
            rows = [{"name": r.get("name"), "private": bool(r.get("private")), "archived": bool(r.get("archived")), "default_branch": r.get("default_branch")} for r in repos]
            print_table(rows, [("name", "Repositorio"), ("private", "Privado"), ("archived", "Archivado"), ("default_branch", "Rama por defecto")])
            return 0
        if args.cmd == "estado":
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=args.visibility, archived=archived)
            analyzer = RepoAnalyzer(gh, owner, target_branch_name=args.branch_objetivo, concurrency=args.concurrency)
            rows = analyzer.summarize(repos)
            if args.output == "table":
                print_table(
                    rows,
                    [
                        ("name", "Repositorio"),
                        ("private", "Privado"),
                        ("archived", "Archivado"),
                        ("default_branch", "Rama"),
                        ("branch_name_ok", "Nombre OK"),
                        ("branch_protected", "Protegida"),
                        ("has_readme", "README"),
                        ("has_license", "LICENSE"),
                        ("has_workflows", "Workflows"),
                        ("has_codeowners", "CODEOWNERS"),
                        ("has_editorconfig", ".editorconfig"),
                        ("issues_enabled", "Issues"),
                        ("open_issues", "Abiertas"),
                        ("error", "Error"),
                    ],
                )
            else:
                out_dir = Path(args.out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = int(time.time())
                if args.output == "json":
                    write_json(rows, out_dir / f"estado_{owner}_{ts}.json")
                elif args.output == "csv":
                    write_csv(rows, out_dir / f"estado_{owner}_{ts}.csv")
                elif args.output == "html":
                    write_html(rows, out_dir / f"estado_{owner}_{ts}.html", title=f"Estado repos {owner}")
            return 0
        if args.cmd == "descargar":
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=args.visibility, archived=archived)
            names = [r.get("name") for r in repos]
            selected = args.repos if args.repos else names
            downloader = RepoDownloader(gh, owner, Path(args.dest))
            results = downloader.download(selected, ref=args.ref)
            for name, path in results:
                print(f"{name}: {path}")
            return 0
        if args.cmd == "optimizar":
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=args.visibility, archived=archived)
            names = set([r.get("name") for r in repos])
            selected = names if not args.repos else [n for n in args.repos if n in names]
            subset = [r for r in repos if r.get("name") in selected]
            analyzer = RepoAnalyzer(gh, owner, target_branch_name=args.branch_objetivo, concurrency=args.concurrency)
            status_rows = analyzer.summarize(subset)
            templates: Dict[str, str] = {}
            cfg_tpl = cfg.get("templates") if isinstance(cfg.get("templates"), dict) else {}
            if isinstance(cfg_tpl, dict):
                for k in ("readme", "license", "codeowners", "editorconfig", "workflow_ci"):
                    v = cfg_tpl.get(k)
                    if v and k not in templates and Path(v).exists():
                        templates[k] = Path(v).read_text(encoding="utf-8")
            if args.plantilla_readme and Path(args.plantilla_readme).exists():
                templates["readme"] = Path(args.plantilla_readme).read_text(encoding="utf-8")
            if args.plantilla_license and Path(args.plantilla_license).exists():
                templates["license"] = Path(args.plantilla_license).read_text(encoding="utf-8")
            if args.plantilla_codeowners and Path(args.plantilla_codeowners).exists():
                templates["codeowners"] = Path(args.plantilla_codeowners).read_text(encoding="utf-8")
            if args.plantilla_editorconfig and Path(args.plantilla_editorconfig).exists():
                templates["editorconfig"] = Path(args.plantilla_editorconfig).read_text(encoding="utf-8")
            if "readme" not in templates or "license" not in templates or "codeowners" not in templates or "editorconfig" not in templates:
                base = Path(__file__).parent / "plantillas"
                if "readme" not in templates:
                    p = base / "README.md"
                    if p.exists():
                        templates["readme"] = p.read_text(encoding="utf-8")
                if "license" not in templates:
                    p = base / "LICENSE_MIT.txt"
                    if p.exists():
                        templates["license"] = p.read_text(encoding="utf-8")
                if "codeowners" not in templates:
                    p = base / "CODEOWNERS"
                    if p.exists():
                        templates["codeowners"] = p.read_text(encoding="utf-8")
                if "editorconfig" not in templates:
                    p = base / ".editorconfig"
                    if p.exists():
                        templates["editorconfig"] = p.read_text(encoding="utf-8")
                p = base / "ci.yml"
                if p.exists():
                    templates["workflow_ci"] = p.read_text(encoding="utf-8")
            optimizer = Optimizer(gh, owner, target_branch_name=args.branch_objetivo, templates=templates)
            plans = optimizer.plan(
                status_rows,
                args.crear_readme,
                args.crear_license,
                args.asegurar_workflows,
                args.habilitar_issues,
                create_codeowners=args.crear_codeowners,
                create_editorconfig=args.crear_editorconfig,
                protect_branch=args.proteger_branch,
                rename_branch=args.renombrar_branch,
                ensure_main=args.asegurar_main,
                default_main=args.default_main,
            )
            want_diffs = bool(args.dry_run and (args.crear_readme or args.crear_license or args.asegurar_workflows or args.crear_codeowners or args.crear_editorconfig))
            if args.dry_run or not (
                args.crear_readme
                or args.crear_license
                or args.asegurar_workflows
                or args.habilitar_issues
                or args.crear_codeowners
                or args.crear_editorconfig
                or args.proteger_branch
                or args.renombrar_branch
                or args.asegurar_main
                or args.default_main
                or args.crear_issue_housekeeping
                or args.crear_pr_housekeeping
            ):
                preview_rows = None
                if want_diffs and plans:
                    default_branches = {r.get("name"): (r.get("default_branch") or args.branch_objetivo) for r in status_rows if isinstance(r, dict) and r.get("name")}
                    preview_rows = optimizer.preview(plans, default_branches)
                if args.output == "table":
                    if preview_rows is not None:
                        slim = [{"name": r["name"], "actions": r.get("actions", []), "diffs": len([d for d in r.get("diffs", []) if d.get("diff")])} for r in preview_rows]
                        print_table(slim, [("name", "Repositorio"), ("actions", "Acciones"), ("diffs", "Diffs")])
                    else:
                        print_table(plans, [("name", "Repositorio"), ("actions", "Acciones")])
                else:
                    out_dir = Path(args.out_dir)
                    ts = int(time.time())
                    payload = preview_rows if preview_rows is not None else plans
                    if args.output == "json":
                        write_json(payload, out_dir / f"plan_{owner}_{ts}.json")
                    elif args.output == "csv":
                        write_csv(payload, out_dir / f"plan_{owner}_{ts}.csv")
                    elif args.output == "html":
                        write_html(payload, out_dir / f"plan_{owner}_{ts}.html", title=f"Plan optimización {owner}")
                return 0
            results: List[Dict] = []
            if args.crear_pr_housekeeping and args.branch and status_rows:
                base_branch = status_rows[0].get("default_branch") or args.branch_objetivo
                results = optimizer.execute(plans, branch=args.branch, open_pr=True, base_branch=base_branch)
            else:
                results = optimizer.execute(plans, branch=None)
            if args.crear_issue_housekeeping and results:
                for r in results:
                    if r.get("executed"):
                        title = "Housekeeping"
                        body = "Se proponen o ejecutan acciones: " + ", ".join(r.get("executed", []))
                        gh.create_issue(owner, r["name"], title, body)
            if args.output == "table":
                print_table(results, [("name", "Repositorio"), ("executed", "Ejecutadas")])
            else:
                out_dir = Path(args.out_dir)
                ts = int(time.time())
                if args.output == "json":
                    write_json(results, out_dir / f"resultado_{owner}_{ts}.json")
                elif args.output == "csv":
                    write_csv(results, out_dir / f"resultado_{owner}_{ts}.csv")
                elif args.output == "html":
                    write_html(results, out_dir / f"resultado_{owner}_{ts}.html", title=f"Resultados optimización {owner}")
            return 0
        if args.cmd == "analizar":
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=args.visibility, archived=archived)
            names = [r.get("name") for r in repos]
            selected = set(args.repos) if args.repos else set(names)
            subset = [r for r in repos if r.get("name") in selected]
            deep = DeepAnalyzer(gh, owner)
            rows = [deep.analyze(r) for r in subset]
            if args.output == "table":
                print_table(rows, [("name", "Repositorio"), ("languages", "Lenguajes"), ("techs", "Tecnologías"), ("has_dockerfile", "Docker"), ("has_tests", "Tests"), ("files_count", "Ficheros")])
            else:
                out_dir = Path(args.out_dir)
                ts = int(time.time())
                if args.output == "json":
                    write_json(rows, out_dir / f"analisis_{owner}_{ts}.json")
                elif args.output == "csv":
                    write_csv(rows, out_dir / f"analisis_{owner}_{ts}.csv")
                elif args.output == "html":
                    write_html(rows, out_dir / f"analisis_{owner}_{ts}.html", title=f"Análisis repos {owner}")
            return 0
        if args.cmd == "mejorar":
            repos = gh.list_repos(owner, owner_type=owner_type, visibility=args.visibility, archived=archived)
            names = [r.get("name") for r in repos]
            selected = set(args.repos) if args.repos else set(names)
            subset = [r for r in repos if r.get("name") in selected]
            base = Path(__file__).parent / "plantillas"
            deep = DeepAnalyzer(gh, owner)
            advisor = LicenseAdvisor()
            readme_gen = ReadmeGenerator(owner)
            results: List[Dict] = []
            import difflib
            for r in subset:
                name = r.get("name")
                analysis = deep.analyze(r)
                executed: List[str] = []
                diffs: List[Dict] = []
                def preview_or_apply(path: str, content: str, message: str) -> bool:
                    if args.dry_run:
                        old = gh.get_file_text(owner, name, path, r.get("default_branch")) or ""
                        new = content
                        diff_text = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
                        diffs.append({"path": path, "message": message, "diff": diff_text or f"CREATE {path}"})
                        return True
                    return gh.create_or_update_file(owner, name, path, content, message)
                if args.recomendar_licencia:
                    rec = advisor.recommend(r, analysis)
                    executed.append(f"license_recommended:{rec}")
                    if args.aplicar_licencia:
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
                if args.auto_topics:
                    add_topics = set(analysis.get("techs", []))
                    cur_topics = set(gh.get_topics(owner, name))
                    extra = cfg.get("topics_extra") if isinstance(cfg.get("topics_extra"), list) else []
                    merged = sorted(cur_topics | add_topics | set([t for t in extra if isinstance(t, str) and t.strip()]))
                    if merged != list(cur_topics):
                        if gh.set_topics(owner, name, merged):
                            executed.append("topics_updated")
                if args.auto_description and not (r.get("description") or "").strip():
                    langs = ", ".join(sorted((analysis.get("languages") or {}).keys()))
                    desc = f"{name}: {langs}"
                    if args.dry_run:
                        executed.append("description_set(dry)")
                    elif gh.update_repo_description(owner, name, description=desc):
                        executed.append("description_set")
                if args.gitignore_auto and not gh.has_file(owner, name, ".gitignore", r.get("default_branch")):
                    gi_content = None
                    langs = set((analysis.get("languages") or {}).keys())
                    if "Python" in langs and (base / ".gitignore_python").exists():
                        gi_content = (base / ".gitignore_python").read_text(encoding="utf-8")
                    elif ("JavaScript" in langs or "TypeScript" in langs) and (base / ".gitignore_node").exists():
                        gi_content = (base / ".gitignore_node").read_text(encoding="utf-8")
                    if gi_content:
                        if preview_or_apply(".gitignore", gi_content, "Add .gitignore"):
                            executed.append("gitignore_added")
                if args.issues_templates and not gh.has_file(owner, name, ".github/ISSUE_TEMPLATE/bug_report.md", r.get("default_branch")):
                    p = base / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
                    if p.exists():
                        if preview_or_apply(".github/ISSUE_TEMPLATE/bug_report.md", p.read_text(encoding="utf-8"), "Add bug report template"):
                            executed.append("issue_template_bug")
                    p2 = base / ".github" / "ISSUE_TEMPLATE" / "feature_request.md"
                    if p2.exists() and not gh.has_file(owner, name, ".github/ISSUE_TEMPLATE/feature_request.md", r.get("default_branch")):
                        if preview_or_apply(".github/ISSUE_TEMPLATE/feature_request.md", p2.read_text(encoding="utf-8"), "Add feature request template"):
                            executed.append("issue_template_feature")
                if args.pr_template and not gh.has_file(owner, name, ".github/PULL_REQUEST_TEMPLATE.md", r.get("default_branch")):
                    p = base / ".github" / "PULL_REQUEST_TEMPLATE.md"
                    if p.exists():
                        if preview_or_apply(".github/PULL_REQUEST_TEMPLATE.md", p.read_text(encoding="utf-8"), "Add PR template"):
                            executed.append("pr_template")
                if args.generar_readme_auto:
                    exists = gh.has_readme(owner, name)
                    if args.forzar_readme or not exists:
                        content = readme_gen.build(r, analysis)
                        if preview_or_apply("README.md", content, "Generate README"):
                            executed.append("readme_generated")
                if args.workflows_ai and not gh.has_workflows(owner, name):
                    wf = None
                    langs = set((analysis.get("languages") or {}).keys())
                    if analysis.get("node_monorepo") and (base / "workflows" / "ci_monorepo_node.yml").exists():
                        wf = (base / "workflows" / "ci_monorepo_node.yml").read_text(encoding="utf-8")
                    elif "Go" in langs and (base / "workflows" / "ci_go.yml").exists():
                        wf = (base / "workflows" / "ci_go.yml").read_text(encoding="utf-8")
                    elif "Rust" in langs and (base / "workflows" / "ci_rust.yml").exists():
                        wf = (base / "workflows" / "ci_rust.yml").read_text(encoding="utf-8")
                    elif "Java" in langs and (base / "workflows" / "ci_java.yml").exists():
                        wf = (base / "workflows" / "ci_java.yml").read_text(encoding="utf-8")
                    elif "Python" in langs and (base / "workflows" / "ci_python.yml").exists():
                        wf = (base / "workflows" / "ci_python.yml").read_text(encoding="utf-8")
                    elif ("JavaScript" in langs or "TypeScript" in langs) and (base / "workflows" / "ci_node.yml").exists():
                        wf = (base / "workflows" / "ci_node.yml").read_text(encoding="utf-8")
                    if wf:
                        if preview_or_apply(".github/workflows/ci.yml", wf, "Add CI workflow AI"):
                            executed.append("workflow_ai_added")
                if args.pages_static:
                    pages_wf_path = ".github/workflows/pages.yml"
                    has_pages_wf = gh.has_file(owner, name, pages_wf_path, r.get("default_branch"))
                    if not has_pages_wf and (base / "workflows" / "pages_static.yml").exists():
                        wf = (base / "workflows" / "pages_static.yml").read_text(encoding="utf-8")
                        if preview_or_apply(pages_wf_path, wf, "Add Pages workflow"):
                            executed.append("pages_workflow_added")
                    root = args.pages_root.strip("/")
                    use_docsify = False
                    if args.pages_template == "docsify":
                        use_docsify = True
                        root = "docs"
                    index_path = f"{root}/index.html"
                    if not gh.has_file(owner, name, index_path, r.get("default_branch")):
                        if use_docsify and (base / "pages" / "docsify_index.html").exists():
                            html = (base / "pages" / "docsify_index.html").read_text(encoding="utf-8")
                        else:
                            html = f"<!doctype html><meta charset='utf-8'><title>{name}</title><h1>{name}</h1><p>Publicado con GitHub Pages.</p>"
                        if preview_or_apply(index_path, html, "Add Pages public index"):
                            executed.append("pages_index_added")
                    if use_docsify:
                        sb_path = f"{root}/_sidebar.md"
                        if not gh.has_file(owner, name, sb_path, r.get("default_branch")):
                            if (base / "pages" / "docsify_sidebar.md").exists():
                                if preview_or_apply(sb_path, (base / "pages" / "docsify_sidebar.md").read_text(encoding="utf-8"), "Add Docsify _sidebar.md"):
                                    executed.append("pages_docsify_sidebar")
                        readme_path = f"{root}/README.md"
                        if not gh.has_file(owner, name, readme_path, r.get("default_branch")):
                            if (base / "pages" / "docsify_readme.md").exists():
                                if preview_or_apply(readme_path, (base / "pages" / "docsify_readme.md").read_text(encoding="utf-8"), "Add Docsify README.md"):
                                    executed.append("pages_docsify_readme")
                    homepage = r.get("homepage") or ""
                    if not homepage:
                        url = f"https://{owner}.github.io/{name}"
                        if args.dry_run:
                            executed.append("homepage_set(dry)")
                        elif gh.update_repo_description(owner, name, homepage=url):
                            executed.append("homepage_set")
                row = {"name": name, "executed": executed}
                if args.dry_run:
                    row["diffs"] = diffs
                results.append(row)
            if args.output == "table":
                print_table(results, [("name", "Repositorio"), ("executed", "Acciones")])
            else:
                out_dir = Path(args.out_dir)
                ts = int(time.time())
                if args.output == "json":
                    write_json(results, out_dir / f"mejoras_{owner}_{ts}.json")
                elif args.output == "csv":
                    write_csv(results, out_dir / f"mejoras_{owner}_{ts}.csv")
                elif args.output == "html":
                    write_html(results, out_dir / f"mejoras_{owner}_{ts}.html", title=f"Mejoras repos {owner}")
            return 0
        return 1
    finally:
        if audit:
            audit.close()


if __name__ == "__main__":
    sys.exit(main())
