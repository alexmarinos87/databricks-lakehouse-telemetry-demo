"""Keep the runtime handoff aligned with actual notebook entrypoints and semantics."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs/spark_runtime_evidence.md"


class RuntimeEvidenceDocumentationTest(unittest.TestCase):
    def test_documented_governed_functions_are_imported_and_called_by_notebooks(self):
        text = DOCUMENT.read_text(encoding="utf-8")
        for filename, function in (
            ("03_gold_models.py", "build_governed_gold_frames"),
            ("07_warehouse_model.py", "build_governed_warehouse_frames"),
        ):
            with self.subTest(notebook=filename):
                tree = ast.parse((ROOT / "notebooks" / filename).read_text(encoding="utf-8"))
                imports = {
                    alias.asname or alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module == "lakehouse_demo.downtime_pipeline"
                    for alias in node.names
                }
                calls = {node.func.id for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
                self.assertIn(function, imports)
                self.assertIn(function, calls)
                self.assertIn(f"`{filename}` calls `{function}`", text)

    def test_documented_semantic_version_matches_the_literal_source_constant(self):
        tree = ast.parse((ROOT / "src/lakehouse_demo/spark_downtime_semantics.py").read_text(encoding="utf-8"))
        versions = [ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "SEMANTIC_VERSION" for target in node.targets)]
        self.assertEqual(1, len(versions))
        text = DOCUMENT.read_text(encoding="utf-8")
        self.assertIn(f"`{versions[0]}`", text)
        self.assertIn("reconciled load may exceed 100%", text)
        for stale in ("business definition is unresolved", "until that rule is decided"):
            self.assertNotIn(stale, text)

    def test_fixture_evidence_does_not_claim_workspace_or_decimal_equivalence(self):
        text = DOCUMENT.read_text(encoding="utf-8")
        self.assertIn("tests_runtime/test_spark_fixture_reconciliation_runtime.py", text)
        self.assertTrue((ROOT / "tests_runtime/test_spark_fixture_reconciliation_runtime.py").is_file())
        for boundary in ("not an Auto Loader run", "not a\ngeneral equality guarantee",
                         "`spark_runtime` field remains `not_run`", "manifest-last publication",
                         "Unity Catalog permissions", "rather than a Databricks cluster"):
            self.assertIn(boundary, text)


if __name__ == "__main__":
    unittest.main()
