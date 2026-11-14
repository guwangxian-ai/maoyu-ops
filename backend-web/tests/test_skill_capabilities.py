"""Keep the distributed capability map aligned with real routes and tools."""
import ast
from pathlib import Path
import re
import unittest

from fastapi import FastAPI
from app.api import api_router
from app.mcp.catalog import ALLOW, build_catalog
from app.mcp.onboarding import CAPABILITY_GUIDE, SKILL_FILES, SKILL_ROOT, guide
from app.mcp.server import GUIDE


class SkillCapabilityTests(unittest.TestCase):
    def test_every_route_module_has_a_documented_entry_or_explicit_boundary(self):
        index = (SKILL_ROOT / "references/capabilities.md").read_text()
        boundary = (SKILL_ROOT / "references/project-admin.md").read_text()
        routes = Path(__file__).resolve().parents[1] / "app/api/routes"
        for path in routes.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            has_routes = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in {"get", "post", "put", "delete", "patch", "websocket"}
                for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                for n in fn.decorator_list)
            if has_routes:
                self.assertIn(f"`{path.stem}`", index + boundary, path.name)
        for module in ALLOW:
            self.assertIn(f"`{module}`", index, module)

    def test_documented_entry_tools_exist_in_real_catalog(self):
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        catalog = build_catalog(app.routes)
        index = (SKILL_ROOT / "references/capabilities.md").read_text()
        entries = re.findall(r"`([a-z][a-z0-9_]+)`", index)
        prefixes = {name.split("_", 1)[0] for name in catalog}
        tools = [name for name in entries if "_" in name and name.split("_", 1)[0] in prefixes
                 and name not in ALLOW]
        self.assertGreater(len(tools), 40)
        for name in tools:
            self.assertIn(name, catalog, name)

    def test_workflow_references_are_in_public_package_and_shared_instructions(self):
        for name in ("references/capabilities.md", "references/accounts-replies.md", "references/project-admin.md"):
            self.assertIn(name, SKILL_FILES)
            self.assertTrue((SKILL_ROOT / name).is_file())
        for name in SKILL_FILES:
            if not name.endswith(".md"):
                continue
            path = SKILL_ROOT / name
            for link in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                target = link.split("#", 1)[0]
                if not target or "://" in target or target.startswith("/"):
                    continue
                resolved = (path.parent / target).resolve().relative_to(SKILL_ROOT.resolve()).as_posix()
                self.assertIn(resolved, SKILL_FILES, f"{name}: {link}")
        self.assertIn(CAPABILITY_GUIDE, GUIDE)
        self.assertIn(CAPABILITY_GUIDE, guide())


if __name__ == "__main__":
    unittest.main()
