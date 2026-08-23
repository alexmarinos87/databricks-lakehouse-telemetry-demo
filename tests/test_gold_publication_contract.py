import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_NOTEBOOK = REPO_ROOT / "notebooks" / "03_gold_models.py"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
GRANT_HELPER = REPO_ROOT / "scripts" / "apply_uc_grants.py"
CHANGE_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "versioned_gold_publication.md"
)
RECOVERY_RUNBOOK = (
    REPO_ROOT / "docs" / "runbooks" / "gold_publication_recovery.md"
)


GOLD_DATASETS = (
    "gold_machine_uptime",
    "gold_failure_events",
    "gold_maintenance_costs",
    "gold_parts_usage",
    "gold_client_asset_summary",
)


class GoldPublicationContractTest(unittest.TestCase):
    def test_gold_notebook_versions_all_five_outputs_and_commits_last(self):
        source = GOLD_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn('f"{dataset_name}_history"', source)
        for dataset in GOLD_DATASETS:
            self.assertIn(dataset, source)
        for token in (
            "gold_publication_manifest",
            "gold_publication_run_id",
            "build_family_manifest",
            "audit_family_publication",
            "publication_state=STATE_FAILED",
            "publication_state=STATE_COMMITTED",
            "the incomplete generation remains hidden from current views",
        ):
            self.assertIn(token, source)

        history_loop = source.index("for dataset_name in sorted(current_names):")
        reconciliation = source.index('failure_stage = "reconcile_persisted_history"')
        commit = source.index('failure_stage = "commit_manifest"')

        self.assertLess(history_loop, reconciliation)
        self.assertLess(reconciliation, commit)
        self.assertNotIn('.mode("overwrite")', source)
        self.assertNotIn("CREATE OR REPLACE VIEW", source)
        self.assertIn(
            'verb = "ALTER VIEW" if relation_type == "VIEW" else "CREATE VIEW"',
            source,
        )

    def test_legacy_current_gold_tables_fail_before_manifest_mutation(self):
        source = GOLD_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("def _preflight_relations():", source)
        self.assertIn("Preserve and rename legacy physical ", source)
        self.assertIn("tables before retrying.", source)
        self.assertLess(
            source.index("_preflight_relations()"),
            source.index("_merge_manifest(started_manifest)"),
        )

    def test_workflow_passes_one_job_run_identity_to_gold(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        gold_task = source[
            source.index("- task_key: gold_models") :
            source.index("- task_key: quality_checks")
        ]

        self.assertIn('gold_publication_run_id: "job_{{job.run_id}}"', gold_task)

    def test_analyst_grants_use_views_and_exclude_raw_gold_histories(self):
        source = GRANT_HELPER.read_text(encoding="utf-8")
        tables = source[
            source.index("REPORTING_TABLES = (") :
            source.index("\n)\n", source.index("REPORTING_TABLES = (")) + 2
        ]
        views = source[
            source.index("REPORTING_VIEWS = (") :
            source.index("\n)\n", source.index("REPORTING_VIEWS = (")) + 2
        ]

        for dataset in GOLD_DATASETS:
            self.assertIn(f'"{dataset}"', views)
            self.assertNotIn(f'"{dataset}"', tables)
            self.assertNotIn(f'"{dataset}_history"', source)
        self.assertNotIn('"gold_publication_manifest"', source)

    def test_docs_cover_five_output_migration_retention_and_rollback(self):
        brief = CHANGE_BRIEF.read_text(encoding="utf-8").lower()
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")

        for token in (
            "manifest-last",
            "five",
            "not cross-table acid",
            "legacy physical",
            "retention",
            "rollback",
        ):
            self.assertIn(token, brief)

        for token in (
            "STARTED",
            "COMMITTED",
            "FAILED",
            "five current views",
            "Delta version",
            "Do not delete a committed run",
        ):
            self.assertIn(token, runbook)


if __name__ == "__main__":
    unittest.main()
