from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = REPO_ROOT / "databricks.yml"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
PIPELINE = REPO_ROOT / "resources" / "lakehouse_quality_expectations.yml"
ACCESS_CONTROLS = REPO_ROOT / "resources" / "access_controls.yml"
CONTRACT = REPO_ROOT / "config" / "identity_privilege_contract.json"
DOCUMENTATION = REPO_ROOT / "docs" / "identity_model.md"
VERIFIER = REPO_ROOT / "scripts" / "verify_identity_privilege_evidence.py"
CHANGE_BRIEF = (
    REPO_ROOT
    / "docs"
    / "change_briefs"
    / "verify_identity_privilege_evidence.md"
)


class RuntimeIdentityContractTest(unittest.TestCase):
    def test_bundle_defines_distinct_deployment_and_runtime_identities(self):
        bundle = BUNDLE.read_text(encoding="utf-8")

        self.assertIn("ci_service_principal_name:", bundle)
        self.assertIn("default: lakehouse-demo-ci", bundle)
        self.assertIn("runtime_service_principal_name:", bundle)
        self.assertIn("default: lakehouse-demo-runtime", bundle)
        self.assertNotEqual("lakehouse-demo-ci", "lakehouse-demo-runtime")

    def test_job_and_pipeline_run_as_runtime_not_deployer(self):
        for path in (WORKFLOW, PIPELINE):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("run_as:", source)
                self.assertIn(
                    "service_principal_name: ${var.runtime_service_principal_name}",
                    source,
                )
                run_as_block = source[
                    source.index("run_as:") : source.index("permissions:")
                ]
                self.assertNotIn("ci_service_principal_name", run_as_block)

    def test_deployer_manages_resources_but_runtime_executes(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        pipeline = PIPELINE.read_text(encoding="utf-8")

        for source in (workflow, pipeline):
            self.assertIn(
                "service_principal_name: ${var.ci_service_principal_name}", source
            )
            self.assertIn("level: CAN_MANAGE", source)
            self.assertIn(
                "service_principal_name: ${var.runtime_service_principal_name}",
                source,
            )
        self.assertIn("level: CAN_VIEW", workflow)
        self.assertIn("level: CAN_RUN", pipeline)

    def test_schema_grants_remove_curated_data_access_from_deployer(self):
        source = ACCESS_CONTROLS.read_text(encoding="utf-8")
        deployer_start = source.index("- principal: ${var.ci_service_principal_name}")
        runtime_start = source.index(
            "- principal: ${var.runtime_service_principal_name}", deployer_start
        )
        deployer_schema = source[deployer_start:runtime_start]
        runtime_schema_end = source.index("\n\n  volumes:", runtime_start)
        runtime_schema = source[runtime_start:runtime_schema_end]

        self.assertIn("MANAGE", deployer_schema)
        self.assertIn("USE_SCHEMA", deployer_schema)
        self.assertNotIn("MODIFY", deployer_schema)
        self.assertNotIn("SELECT", deployer_schema)
        for privilege in (
            "USE_SCHEMA",
            "CREATE_TABLE",
            "CREATE_VOLUME",
            "MODIFY",
            "SELECT",
            "READ_VOLUME",
            "WRITE_VOLUME",
        ):
            self.assertIn(privilege, runtime_schema)

    def test_machine_readable_contract_contains_required_and_denied_evidence(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

        self.assertEqual(1, contract["schema_version"])
        deployment = contract["identities"]["deployment"]
        runtime = contract["identities"]["runtime"]
        self.assertNotEqual(deployment["principal_name"], runtime["principal_name"])
        self.assertIn("select_curated_tables", deployment["denied_capabilities"])
        self.assertIn("bundle_deploy", runtime["denied_capabilities"])
        self.assertGreaterEqual(len(contract["required_external_evidence"]), 5)
        self.assertEqual(
            "upload_bounded_synthetic_fixture",
            contract["known_exception"]["capability"],
        )

    def test_live_evidence_verifier_is_bounded_offline_and_fail_closed(self):
        source = VERIFIER.read_text(encoding="utf-8")

        for token in (
            "identity_privilege_contract.json",
            "REQUIRED_EVIDENCE_RULES",
            "evidence_target_must_be_dev",
            "identity_fingerprints_overlap",
            "required_evidence_missing",
            "required_capability_not_succeeded",
            "expected_denial_not_observed",
            "DEFAULT_MAX_AGE_HOURS",
            "MAX_OBSERVATIONS",
            "identity-privilege-verification.json",
            "identity-privilege-verification.md",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("DATABRICKS_TOKEN", source)
        self.assertNotIn("DATABRICKS_CLIENT_SECRET", source)

    def test_documentation_names_service_principal_user_and_live_denials(self):
        documentation = "\n".join(
            (
                DOCUMENTATION.read_text(encoding="utf-8"),
                CHANGE_BRIEF.read_text(encoding="utf-8"),
            )
        )

        self.assertIn("Service Principal User", documentation)
        self.assertIn("deployment principal is denied `SELECT`", documentation)
        self.assertIn("runtime principal is denied bundle deployment", documentation)
        self.assertIn("A successful repository CI run or bundle plan is not live", documentation)
        self.assertIn("optional synthetic fixture upload", documentation)
        self.assertIn("verify_identity_privilege_evidence.py", documentation)
        self.assertIn("denied_live_attempt", documentation)
        self.assertIn("identity-privilege-verification.json", documentation)
        self.assertIn("development evidence only", documentation)


if __name__ == "__main__":
    unittest.main()
