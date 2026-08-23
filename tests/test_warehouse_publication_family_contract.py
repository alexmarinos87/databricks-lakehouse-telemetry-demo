import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE_NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
GRANT_HELPER = REPO_ROOT / "scripts" / "apply_uc_grants.py"
CHANGE_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "versioned_warehouse_publication.md"
)
RECOVERY_RUNBOOK = (
    REPO_ROOT / "docs" / "runbooks" / "warehouse_publication_recovery.md"
)


WAREHOUSE_RELATIONS = (
    "dim_client",
    "dim_date",
    "dim_fault",
    "dim_machine",
    "dim_model",
    "dim_site",
    "fact_machine_failure_event",
    "fact_machine_uptime_daily",
)


class WarehousePublicationFamilyContractTest(unittest.TestCase):
    def test_notebook_audits_then_versions_all_eight_outputs(self):
        source = WAREHOUSE_NOTEBOOK.read_text(encoding="utf-8")

        for relation in WAREHOUSE_RELATIONS:
            self.assertIn(relation, source)
        for token in (
            "warehouse_publication_manifest",
            "warehouse_publication_run_id",
            "build_family_manifest",
            "audit_family_publication",
            "audit_warehouse_publication",
            "publication_state=STATE_FAILED",
            "publication_state=STATE_COMMITTED",
            "the incomplete generation remains hidden from current views",
        ):
            self.assertIn(token, source)

        warehouse_audit = source.index("findings = audit_warehouse_publication(")
        started_manifest = source.index("started_manifest = build_family_manifest(")
        history_loop = source.index("for dataset_name in sorted(current_names):")
        persisted_audit = source.index(
            "persisted_findings = audit_family_publication("
        )
        commit = source.index('failure_stage = "commit_manifest"')

        self.assertLess(warehouse_audit, started_manifest)
        self.assertLess(started_manifest, history_loop)
        self.assertLess(history_loop, persisted_audit)
        self.assertLess(persisted_audit, commit)
        self.assertNotIn('.mode("overwrite")', source)
        self.assertNotIn("CREATE OR REPLACE VIEW", source)

    def test_legacy_physical_dimensions_and_facts_fail_before_mutation(self):
        source = WAREHOUSE_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("def _preflight_relations():", source)
        self.assertIn("Preserve and rename legacy ", source)
        self.assertIn("physical tables before retrying.", source)
        self.assertLess(
            source.index("_preflight_relations()"),
            source.index("_merge_manifest(started_manifest)"),
        )

    def test_workflow_passes_job_run_identity_to_warehouse(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        warehouse_task = source[
            source.index("- task_key: warehouse_model") :
            source.index("- task_key: forecast_validation")
        ]

        self.assertIn(
            'warehouse_publication_run_id: "job_{{job.run_id}}"',
            warehouse_task,
        )

    def test_analyst_grants_use_warehouse_views_not_histories(self):
        source = GRANT_HELPER.read_text(encoding="utf-8")
        tables = source[
            source.index("REPORTING_TABLES = (") :
            source.index("\n)\n", source.index("REPORTING_TABLES = (")) + 2
        ]
        views = source[
            source.index("REPORTING_VIEWS = (") :
            source.index("\n)\n", source.index("REPORTING_VIEWS = (")) + 2
        ]

        for relation in WAREHOUSE_RELATIONS:
            self.assertIn(f'"{relation}"', views)
            self.assertNotIn(f'"{relation}"', tables)
            self.assertNotIn(f'"{relation}_history"', source)
        self.assertNotIn('"warehouse_publication_manifest"', source)

    def test_docs_cover_eight_output_migration_and_recovery(self):
        brief = CHANGE_BRIEF.read_text(encoding="utf-8").lower()
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")

        for token in (
            "eight",
            "manifest-last",
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
            "eight current views",
            "Delta version",
            "Do not delete a committed run",
        ):
            self.assertIn(token, runbook)


if __name__ == "__main__":
    unittest.main()
