import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = REPO_ROOT / "docs" / "arc42-architecture.md"
SOURCE = DOCUMENT.read_text(encoding="utf-8")


def anchor_ids(source):
    return re.findall(r'<a\s+id="([a-z0-9-]+)"\s*></a>', source)


def markdown_link_targets(source):
    return re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", source)


def local_link_path(target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return (DOCUMENT.parent / unquote(parsed.path)).resolve()


def section_between(start_heading, end_heading=None):
    start = SOURCE.index(start_heading) + len(start_heading)
    if end_heading is None:
        return SOURCE[start:]
    end = SOURCE.index(end_heading, start)
    return SOURCE[start:end]


def table_rows_after(heading):
    lines = SOURCE.splitlines()
    start = lines.index(heading) + 1
    rows = []
    in_table = False

    for line in lines[start:]:
        if not line.startswith("|"):
            if in_table:
                break
            continue

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            in_table = True
            continue
        if not in_table:
            in_table = True
            continue
        rows.append(cells)

    return rows


class Arc42ArchitectureContractTest(unittest.TestCase):
    def test_all_twelve_numbered_sections_have_unique_stable_ids(self):
        anchors = anchor_ids(SOURCE)
        self.assertEqual(len(anchors), len(set(anchors)))
        self.assertTrue({f"section-{number}" for number in range(1, 13)}.issubset(anchors))

        numbered_headings = re.findall(r"^## (\d+)\. ", SOURCE, flags=re.MULTILINE)
        self.assertEqual([str(number) for number in range(1, 13)], numbered_headings)

    def test_each_section_links_to_the_official_arc42_reference(self):
        targets = set(markdown_link_targets(SOURCE))
        expected = {f"https://docs.arc42.org/section-{number}/" for number in range(1, 13)}
        self.assertTrue(expected.issubset(targets))

    def test_all_local_markdown_links_resolve_inside_the_repository(self):
        checked = []
        for target in markdown_link_targets(SOURCE):
            path = local_link_path(target)
            if path is None:
                continue

            with self.subTest(target=target):
                self.assertTrue(path.is_relative_to(REPO_ROOT))
                self.assertTrue(path.exists(), f"Broken local architecture link: {target}")
            checked.append(target)

        self.assertTrue(checked)

    def test_level_one_building_blocks_have_structured_interfaces_and_dependencies(self):
        expected_ids = {f"BB-{number:02d}" for number in range(1, 9)}
        rows = table_rows_after("### Level-1 blackboxes")
        by_id = {row[0]: row for row in rows if row and row[0].startswith("BB-")}

        self.assertTrue(expected_ids.issubset(by_id))
        for block_id, row in by_id.items():
            with self.subTest(block_id=block_id):
                self.assertGreaterEqual(len(row), 7)
                self.assertTrue(all(row[:7]))

        expected_anchors = {
            "bb-ingestion",
            "bb-trusted",
            "bb-curated",
            "bb-serving",
            "bb-forecast",
            "bb-quality",
            "bb-platform",
            "bb-assurance",
        }
        self.assertTrue(expected_anchors.issubset(anchor_ids(SOURCE)))

    def test_source_family_map_covers_every_repository_owned_family(self):
        source_family_section = section_between(
            "### Source-family ownership",
            '<a id="section-6"></a>',
        )
        targets = set(markdown_link_targets(source_family_section))
        expected_targets = {
            "../data/",
            "../notebooks/",
            "../src/",
            "../sql/",
            "../resources/",
            "../scripts/",
            "../tests/",
            "../.github/workflows/",
            "../docs/",
            "../databricks.yml",
            "../Dockerfile.ci",
            "../.dockerignore",
            "../README.md",
            "../.gitignore",
        }
        self.assertTrue(expected_targets.issubset(targets))

    def test_representative_runtime_and_deployment_scenarios_are_identified(self):
        anchors = set(anchor_ids(SOURCE))
        expected_runtime = {
            "runtime-happy-path",
            "runtime-invalid-record",
            "runtime-partial-publication",
            "runtime-delivery-failure",
        }
        self.assertTrue(expected_runtime.issubset(anchors))
        self.assertTrue({"deployment-dev", "deployment-prod"}.issubset(anchors))

    def test_change_modularity_defines_the_three_candidate_seams(self):
        self.assertIn("change-modularity", anchor_ids(SOURCE))
        rows = table_rows_after("### Change modularity and candidate seams")
        seam_rows = {row[0]: row for row in rows if row and row[0].startswith("SEAM-")}
        expected_primary_blocks = {
            "SEAM-01": "BB-08",
            "SEAM-02": "BB-01",
            "SEAM-03": "BB-04",
        }

        self.assertTrue(expected_primary_blocks.keys() <= seam_rows.keys())
        for seam_id, primary_block in expected_primary_blocks.items():
            with self.subTest(seam_id=seam_id):
                row = seam_rows[seam_id]
                self.assertGreaterEqual(len(row), 6)
                self.assertEqual(primary_block, row[2])
                self.assertTrue(row[5].startswith("Candidate only"))

    def test_quality_scenarios_are_measurable_and_cover_key_boundaries(self):
        expected_ids = {f"QS-{number:02d}" for number in range(1, 9)}
        rows = table_rows_after("## 10. Quality Requirements")
        by_id = {row[0]: row for row in rows if row and row[0].startswith("QS-")}

        self.assertTrue(expected_ids.issubset(by_id))
        for scenario_id, row in by_id.items():
            with self.subTest(scenario_id=scenario_id):
                self.assertGreaterEqual(len(row), 5)
                self.assertTrue(all(row[:5]))

        required_anchors = {
            "quality-contract",
            "quality-trusted",
            "quality-reconciliation",
            "quality-durable-failure",
            "quality-replay",
            "quality-isolation",
            "quality-forecast",
            "quality-bounded-operations",
        }
        self.assertTrue(required_anchors.issubset(anchor_ids(SOURCE)))

    def test_prioritized_risks_have_evidence_impact_and_mitigation(self):
        expected_categories = {
            "Environment isolation",
            "Resource resolution",
            "Pipeline configuration",
            "Warehouse integrity",
            "Publication recovery",
            "Quality observability",
            "Replay",
            "Forecast semantics",
            "Security boundary",
            "Delivery resilience",
            "Assurance debt",
            "Decision debt",
        }
        rows = table_rows_after("## 11. Risks and Technical Debt")
        risk_rows = [row for row in rows if row and re.fullmatch(r"AR-\d{2}", row[0])]

        self.assertGreaterEqual(len(risk_rows), 12)
        self.assertTrue(expected_categories.issubset({row[2] for row in risk_rows}))
        self.assertIn("Critical", {row[1] for row in risk_rows})
        self.assertIn("High", {row[1] for row in risk_rows})
        self.assertIn("Medium", {row[1] for row in risk_rows})
        for row in risk_rows:
            with self.subTest(risk_id=row[0]):
                self.assertGreaterEqual(len(row), 6)
                self.assertTrue(all(row[:6]))

    def test_architecture_decisions_are_not_presented_as_accepted_adrs(self):
        rows = table_rows_after("## 9. Architecture Decisions")
        decision_rows = [row for row in rows if row and re.fullmatch(r"AD-\d{2}", row[0])]

        self.assertGreaterEqual(len(decision_rows), 7)
        for row in decision_rows:
            with self.subTest(decision_id=row[0]):
                self.assertGreaterEqual(len(row), 5)
                self.assertEqual("Observed/implied; not an accepted ADR", row[2])


if __name__ == "__main__":
    unittest.main()
