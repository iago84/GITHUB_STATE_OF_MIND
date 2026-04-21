import io
import json
import tempfile
import unittest
from unittest.mock import patch
import argparse

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


class TestAuditLogger(unittest.TestCase):
    def test_audit_logger_writes_jsonl(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        audit = gh_manager.AuditLogger(path)
        audit.emit({"owner": "o", "repo": "r", "action": "x", "ok": True})
        audit.close()
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        obj = json.loads(lines[0])
        self.assertEqual(obj["owner"], "o")
        self.assertEqual(obj["repo"], "r")
        self.assertEqual(obj["action"], "x")
        self.assertEqual(obj["ok"], True)
        self.assertIn("ts", obj)


class TestOfflineFixtures(unittest.TestCase):
    def test_offline_list_repos_works(self):
        gh = gh_manager.FixtureGitHubClient("fixtures/sample")
        repos = gh.list_repos("iago84", owner_type="user", visibility="all", archived=None)
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0]["name"], "GITHUB_STATE_OF_MIND")


class TestOptimizerPreview(unittest.TestCase):
    def test_optimizer_preview_includes_diffs(self):
        gh = gh_manager.FixtureGitHubClient("fixtures/sample")
        opt = gh_manager.Optimizer(gh, owner="iago84", templates={"readme": "# {{REPO_NAME}}\n"})
        status_rows = [
            {
                "name": "X",
                "has_readme": False,
                "has_license": False,
                "has_workflows": False,
                "issues_enabled": True,
                "has_codeowners": False,
                "has_editorconfig": False,
                "branch_protected": False,
                "branch_name_ok": True,
                "default_branch": "main",
            }
        ]
        plans = opt.plan(status_rows, create_readme=True, create_license="mit", ensure_workflows=True, enable_issues=False, create_codeowners=True, create_editorconfig=True)
        preview = opt.preview(plans, {"X": "main"})
        self.assertEqual(len(preview), 1)
        diffs = [d for d in preview[0]["diffs"] if d.get("diff")]
        self.assertTrue(any(d.get("path") == "README.md" for d in diffs))


class TestTechInference(unittest.TestCase):
    def test_infer_helm_kubernetes_terraform_monorepo(self):
        deep = gh_manager.DeepAnalyzer(gh_manager.GitHubClient(token=None), owner="x")
        techs = set(
            deep._infer_techs(
                {"Python": 1},
                {},
                [
                    "k8s/deploy.yaml",
                    "Chart.yaml",
                    "values.yaml",
                    "main.tf",
                    "packages/app/package.json",
                    "packages/lib/package.json",
                ],
            )
        )
        self.assertIn("kubernetes", techs)
        self.assertIn("helm", techs)
        self.assertIn("terraform", techs)
        self.assertIn("monorepo", techs)


class TestConfigDefaults(unittest.TestCase):
    def test_apply_config_defaults_sets_missing_flags(self):
        args = argparse.Namespace(auto_topics=False, visibility="all")
        cfg = {"defaults": {"auto_topics": True}}
        gh_manager.apply_config_defaults(args, [], cfg)
        self.assertEqual(args.auto_topics, True)


if __name__ == "__main__":
    unittest.main()
