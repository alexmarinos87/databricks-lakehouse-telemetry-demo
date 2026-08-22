import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_forecast_publication.py"
FORECAST_NOTEBOOK = REPO_ROOT / "notebooks" / "05_forecast_validation.py"
EXPECTATIONS_NOTEBOOK = REPO_ROOT / "notebooks" / "06_lakeflow_quality_expectations.py"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"
FORECAST_ASSET = REPO_ROOT / "sql" / "reporting_assets" / "downtime_forecast.sql"
GRANT_HELPER = REPO_ROOT / "scripts" / "apply_uc_grants.py"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
CHANGE_BRIEF = REPO_ROOT / "docs" / "change_briefs" / "versioned_forecast_publication.md"
RECOVERY_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "forecast_publication_recovery.md"


class ForecastPublicationContractTest(unittest.TestCase):
    def test_shared_module_builds_bounded_manifest_and_reconciliation_evidence(self):
        source = PUBLICATION_MODULE.read_text(encoding="utf-8")

        for token in (
            'STATE_STARTED = "STARTED"',
            'STATE_COMMITTED = "COMMITTED"',
            'STATE_FAILED = "FAILED"',
            "def dataset_evidence(",
            "def build_publication_manifest(",
            "def publication_state_for_run(",
            "def audit_publication_run(",
            "def latest_committed_run_id(",
            "def select_latest_committed_frames(",
            '"timeZone": "UTC"',
            "forecast_payload_sha256",
            "validation_payload_sha256",
            "forecast_columns_json",
            "validation_columns_json",
        ):
            self.assertIn(token, source)

        self.assertNotIn("collect_list(", source)

    def test_notebook_preflights_legacy_objects_before_any_publication_write(self):
        source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("def _preflight_relations():", source)
        self.assertIn('relation_type != "VIEW"', source)
        self.assertIn("Preserve and rename the legacy tables", source)
        self.assertLess(
            source.index("_preflight_relations()"),
            source.index("started_manifest = build_publication_manifest("),
        )
        self.assertNotIn('.mode("overwrite")', source)

    def test_notebook_commits_manifest_after_history_reconciliation(self):
        source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")

        started = source.index("_merge_manifest(started_manifest)")
        validation_history = source.index(
            "_replace_history_run(\n            forecast_validation_history_name"
        )
        forecast_history = source.index(
            "_replace_history_run(\n            forecast_history_name"
        )
        audit = source.index("persisted_findings = audit_publication_run(")
        committed = source.index("_merge_manifest(expected_committed_manifest)")
        current_views = source.rindex("_create_current_views()")

        self.assertLess(started, validation_history)
        self.assertLess(validation_history, forecast_history)
        self.assertLess(forecast_history, audit)
        self.assertLess(audit, committed)
        self.assertLess(committed, current_views)

    def test_failed_precommit_run_is_recorded_and_hidden(self):
        source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("publication_state=STATE_FAILED", source)
        self.assertIn("if not publication_committed:", source)
        self.assertIn("the incomplete run remains hidden from current views", source)
        self.assertLess(
            source.index("publication_state=STATE_FAILED"),
            source.rindex("_create_current_views()"),
        )

    def test_current_views_select_latest_commit_without_dropping_privileges(self):
        source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn('verb = "ALTER VIEW" if relation_type == "VIEW" else "CREATE VIEW"', source)
        self.assertNotIn("CREATE OR REPLACE VIEW", source)
        self.assertIn("publication_state = 'COMMITTED'", source)
        self.assertIn("publication_completed_at_utc DESC", source)
        self.assertIn("ROW_NUMBER() OVER", source)
        self.assertIn(
            'forecast_validation_view_name = "gold_downtime_forecast_validation"',
            source,
        )
        self.assertIn('forecast_view_name = "gold_downtime_forecast"', source)

    def test_committed_retry_is_read_only_and_reconciled(self):
        source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")
        committed_branch = source[
            source.index("if existing_state == STATE_COMMITTED:") :
            source.index("else:", source.index("if existing_state == STATE_COMMITTED:"))
        ]

        self.assertIn("audit_publication_run(", committed_branch)
        self.assertNotIn("_replace_history_run(", committed_branch)
        self.assertNotIn("_merge_manifest(", committed_branch)

    def test_expectations_and_reporting_expose_publication_state(self):
        expectations = EXPECTATIONS_NOTEBOOK.read_text(encoding="utf-8")
        reporting = REPORTING_SQL.read_text(encoding="utf-8")
        asset = FORECAST_ASSET.read_text(encoding="utf-8")

        self.assertIn(
            "quality_expectation_forecast_publication_manifest",
            expectations,
        )
        self.assertIn("publication_state_known", expectations)
        self.assertIn("committed_evidence_complete", expectations)
        self.assertIn("gold_downtime_forecast_publication_manifest", reporting)
        self.assertIn("publication_completed_at_utc", reporting)
        self.assertIn("forecast_run_id", asset)
        self.assertIn("forecast_generated_at", asset)

    def test_analysts_receive_typed_current_relations_not_raw_history(self):
        source = GRANT_HELPER.read_text(encoding="utf-8")
        reporting_views = source[
            source.index("REPORTING_VIEWS = (") :
            source.index("\n)\n", source.index("REPORTING_VIEWS = (")) + 2
        ]
        reporting_tables = source[
            source.index("REPORTING_TABLES = (") :
            source.index("\n)\n", source.index("REPORTING_TABLES = (")) + 2
        ]

        self.assertIn('"gold_downtime_forecast_validation"', reporting_views)
        self.assertIn('"gold_downtime_forecast"', reporting_views)
        self.assertNotIn('"gold_downtime_forecast"', reporting_tables)
        self.assertIn('object_type="VIEW"', source)
        self.assertIn('object_type="MATERIALIZED VIEW"', source)
        self.assertNotIn('"gold_downtime_forecast_history"', source)
        self.assertNotIn(
            '"gold_downtime_forecast_validation_history"',
            source,
        )
        self.assertNotIn(
            '"gold_downtime_forecast_publication_manifest"',
            source,
        )

    def test_documents_cover_migration_partial_failure_and_cleanup(self):
        architecture = ARCHITECTURE.read_text(encoding="utf-8")
        change_brief = CHANGE_BRIEF.read_text(encoding="utf-8")
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("gold_downtime_forecast_history", architecture)
        self.assertIn("manifest-last", architecture)
        self.assertIn("legacy", change_brief.lower())
        self.assertIn("STARTED", runbook)
        self.assertIn("COMMITTED", runbook)
        self.assertIn("Do not delete a committed run", runbook)
        self.assertIn("ALTER TABLE", runbook)


if __name__ == "__main__":
    unittest.main()
