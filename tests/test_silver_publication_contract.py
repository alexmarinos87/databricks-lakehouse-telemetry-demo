import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_MODULE = (
    REPO_ROOT / "src" / "lakehouse_demo" / "spark_family_publication.py"
)
SILVER_NOTEBOOK = REPO_ROOT / "notebooks" / "02_silver_transform.py"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
CHANGE_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "versioned_silver_publication.md"
)
RECOVERY_RUNBOOK = (
    REPO_ROOT / "docs" / "runbooks" / "silver_publication_recovery.md"
)


class SilverPublicationContractTest(unittest.TestCase):
    def test_generic_family_module_records_bounded_manifest_evidence(self):
        source = PUBLICATION_MODULE.read_text(encoding="utf-8")

        for token in (
            'STATE_STARTED = "STARTED"',
            'STATE_COMMITTED = "COMMITTED"',
            'STATE_FAILED = "FAILED"',
            "def with_publication_run_id(",
            "def build_family_manifest(",
            "def transition_family_manifest(",
            "def audit_family_publication(",
            "def latest_committed_run_id(",
            "def select_latest_committed_frames(",
            "dataset_evidence_json",
            "history_payload_mismatch",
        ):
            self.assertIn(token, source)

        self.assertNotIn("collect_list(", source)
        self.assertIn("Bounded aggregate evidence", source)

    def test_silver_notebook_uses_manifest_last_visibility(self):
        source = SILVER_NOTEBOOK.read_text(encoding="utf-8")

        for token in (
            "silver_machine_events_history",
            "silver_quarantine_machine_events_history",
            "silver_publication_manifest",
            "silver_publication_run_id",
            "build_family_manifest",
            "audit_family_publication",
            "publication_state=STATE_FAILED",
            "publication_state=STATE_COMMITTED",
            "failed-run quarantine history remains ",
            "available by silver_publication_run_id",
        ):
            self.assertIn(token, source)

        quarantine_write = source.index('failure_stage = "write_quarantine_history"')
        silver_write = source.index('failure_stage = "write_silver_history"')
        reconciliation = source.index('failure_stage = "reconcile_persisted_history"')
        conflict_gate = source.index('failure_stage = "conflicting_event_ids"')
        commit = source.index('failure_stage = "commit_manifest"')

        self.assertLess(quarantine_write, silver_write)
        self.assertLess(silver_write, reconciliation)
        self.assertLess(reconciliation, conflict_gate)
        self.assertLess(conflict_gate, commit)
        self.assertNotIn('.mode("overwrite")', source)
        self.assertNotIn("CREATE OR REPLACE VIEW", source)
        self.assertIn(
            'verb = "ALTER VIEW" if relation_type == "VIEW" else "CREATE VIEW"',
            source,
        )

    def test_legacy_current_tables_fail_before_publication_mutation(self):
        source = SILVER_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("def _preflight_relations():", source)
        self.assertIn("Preserve and rename legacy physical ", source)
        self.assertIn("tables before retrying.", source)
        self.assertLess(
            source.index("_preflight_relations()"),
            source.index("_merge_manifest(started_manifest)"),
        )

    def test_workflow_passes_job_run_identity_to_silver(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        silver_task = source[
            source.index("- task_key: silver_transform") :
            source.index("- task_key: gold_models")
        ]

        self.assertIn('silver_publication_run_id: "job_{{job.run_id}}"', silver_task)

    def test_docs_cover_partial_failure_retention_and_rollback(self):
        brief = CHANGE_BRIEF.read_text(encoding="utf-8").lower()
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")

        for token in (
            "manifest-last",
            "not cross-table acid",
            "retention",
            "legacy physical",
            "rollback",
        ):
            self.assertIn(token, brief)

        for token in (
            "STARTED",
            "COMMITTED",
            "FAILED",
            "quarantine history",
            "Delta version",
            "Do not delete a committed run",
        ):
            self.assertIn(token, runbook)


if __name__ == "__main__":
    unittest.main()
