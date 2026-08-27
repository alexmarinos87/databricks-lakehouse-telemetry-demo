from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "show_evidence_workflow",
    ROOT / "scripts" / "show_evidence_workflow.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class EvidenceWorkflowCatalogueTest(unittest.TestCase):
    def test_default_catalogue_is_ordered_resolved_and_non_authoritative(self):
        catalogue = m.load_catalogue()
        self.assertEqual(list(m.EXPECTED_STAGE_IDS), [stage["stage_id"] for stage in catalogue["stages"]])
        self.assertFalse(catalogue["automatic_action_authorized"])
        for stage in catalogue["stages"]:
            self.assertEqual([], stage["authorizes"])
            for entrypoint in stage["entrypoints"]:
                self.assertTrue((ROOT / entrypoint).is_file())
        self.assertEqual(
            "/databricks-plan dev",
            catalogue["stages"][3]["owner_command"],
        )

    def test_catalogue_and_quickstart_keep_the_same_stage_titles_and_order(self):
        catalogue = m.load_catalogue()
        quickstart = (ROOT / "docs" / "evidence_workflow_quickstart.md").read_text(
            encoding="utf-8"
        )
        positions = [quickstart.index(stage["title"]) for stage in catalogue["stages"]]
        self.assertEqual(sorted(positions), positions)

    def test_text_output_is_deterministic_and_preserves_boundaries(self):
        catalogue = m.load_catalogue()
        rendered = m.render_text(catalogue)
        self.assertEqual(rendered, m.render_text(catalogue))
        for required in (
            "Evidence workflow",
            "[read_only]",
            "[offline_evidence]",
            "[plan_only]",
            "/databricks-plan dev",
            "Automatic action authorized: false",
            "authorizes no apply, deployment or production action",
        ):
            self.assertIn(required, rendered)

    def test_invalid_order_dependency_authority_and_plan_command_fail_closed(self):
        mutations = (
            ("catalogue_stage_order_invalid", lambda doc: doc["stages"][0].update(order=2)),
            ("catalogue_dependency_invalid", lambda doc: doc["stages"][2].update(requires=[])),
            ("catalogue_authority_boundary_invalid", lambda doc: doc["stages"][4].update(authorizes=["apply"])),
            ("catalogue_plan_stage_invalid", lambda doc: doc["stages"][3].update(owner_command="/databricks-plan prod")),
        )
        source = json.loads(m.DEFAULT_CATALOGUE.read_text(encoding="utf-8"))
        for category, mutate in mutations:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                document = json.loads(json.dumps(source))
                mutate(document)
                path = Path(directory) / "catalogue.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(m.CatalogueError, category):
                    m.load_catalogue(path)

    def test_missing_entrypoint_and_symbolic_link_catalogue_fail_closed(self):
        source = json.loads(m.DEFAULT_CATALOGUE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source["stages"][0]["entrypoints"] = ["scripts/missing.py"]
            path = root / "catalogue.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(m.CatalogueError, "entrypoint_missing"):
                m.load_catalogue(path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(m.DEFAULT_CATALOGUE.read_text(encoding="utf-8"), encoding="utf-8")
            link = root / "catalogue.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(m.CatalogueError, "catalogue_not_regular"):
                m.load_catalogue(link)

    def test_source_has_no_network_subprocess_or_mutation_surface(self):
        source = (ROOT / "scripts" / "show_evidence_workflow.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "subprocess",
            "urllib",
            "requests",
            "DATABRICKS_TOKEN",
            "GITHUB_TOKEN",
            "bundle deploy",
            "--apply",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            "automatic_action_authorized",
            "catalogue_authority_boundary_invalid",
            "entrypoint_missing",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
