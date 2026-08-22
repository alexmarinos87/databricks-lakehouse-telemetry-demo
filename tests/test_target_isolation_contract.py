import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = REPO_ROOT / "databricks.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PIPELINE_RESOURCE = REPO_ROOT / "resources" / "lakehouse_quality_expectations.yml"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment.md"

EXPECTED_TARGET_VALUES = {
    "dev": {
        "schema": "lakehouse_demo_dev",
        "source_path": "dbfs:/FileStore/lakehouse_demo/dev/raw_machine_events",
        "checkpoint_path": "dbfs:/FileStore/lakehouse_demo/dev/_checkpoints/bronze_machine_events",
        "schema_location": "dbfs:/FileStore/lakehouse_demo/dev/_schemas/bronze_machine_events",
        "unity_catalog_volume": "lakehouse_demo_dev_files",
        "azure_source_path": "lakehouse_demo/dev/raw_machine_events",
        "azure_checkpoint_path": "lakehouse_demo/dev/_checkpoints/bronze_machine_events",
        "azure_schema_location": "lakehouse_demo/dev/_schemas/bronze_machine_events",
    },
    "prod": {
        "schema": "lakehouse_demo_prod",
        "source_path": "dbfs:/FileStore/lakehouse_demo/prod/raw_machine_events",
        "checkpoint_path": "dbfs:/FileStore/lakehouse_demo/prod/_checkpoints/bronze_machine_events",
        "schema_location": "dbfs:/FileStore/lakehouse_demo/prod/_schemas/bronze_machine_events",
        "unity_catalog_volume": "lakehouse_demo_prod_files",
        "azure_source_path": "lakehouse_demo/prod/raw_machine_events",
        "azure_checkpoint_path": "lakehouse_demo/prod/_checkpoints/bronze_machine_events",
        "azure_schema_location": "lakehouse_demo/prod/_schemas/bronze_machine_events",
    },
}


def target_block(bundle: str, target: str) -> str:
    targets_start = bundle.index("targets:\n")
    target_start = bundle.index(f"  {target}:\n", targets_start)
    next_target = re.search(
        r"^  [a-zA-Z0-9_-]+:\n", bundle[target_start + 1 :], re.MULTILINE
    )
    if next_target is None:
        return bundle[target_start:]
    return bundle[target_start : target_start + 1 + next_target.start()]


def target_value(block: str, key: str) -> str:
    match = re.search(rf"^      {re.escape(key)}:\s*(.*)$", block, re.MULTILINE)
    if match is None:
        raise AssertionError(f"Missing target value: {key}")
    return match.group(1).strip()


class TargetIsolationContractTest(unittest.TestCase):
    def test_dev_and_prod_have_disjoint_writable_namespaces(self):
        bundle = BUNDLE.read_text(encoding="utf-8")
        observed = {}

        for target, expected_values in EXPECTED_TARGET_VALUES.items():
            block = target_block(bundle, target)
            observed[target] = {
                key: target_value(block, key) for key in expected_values
            }
            self.assertEqual(expected_values, observed[target])

        for key in EXPECTED_TARGET_VALUES["dev"]:
            with self.subTest(variable=key):
                self.assertNotEqual(observed["dev"][key], observed["prod"][key])

    def test_target_presets_control_names_and_pipeline_mode(self):
        bundle = BUNDLE.read_text(encoding="utf-8")
        dev = target_block(bundle, "dev")
        prod = target_block(bundle, "prod")

        self.assertEqual('""', target_value(dev, "name_prefix"))
        self.assertEqual('""', target_value(prod, "name_prefix"))
        self.assertEqual("true", target_value(dev, "pipelines_development"))
        self.assertEqual("false", target_value(prod, "pipelines_development"))
        self.assertEqual("PAUSED", target_value(dev, "trigger_pause_status"))

    def test_expectation_pipeline_uses_supported_edition_without_fixed_mode(self):
        pipeline = PIPELINE_RESOURCE.read_text(encoding="utf-8")

        self.assertIn("edition: ADVANCED", pipeline)
        self.assertNotIn("edition: CORE", pipeline)
        self.assertIsNone(re.search(r"^\s+development:\s*", pipeline, re.MULTILINE))

    def test_deploy_workflow_selects_target_specific_values(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        for variable in [
            "DATABRICKS_DEV_CATALOG",
            "DATABRICKS_DEV_SCHEMA",
            "DATABRICKS_DEV_VOLUME",
            "DATABRICKS_PROD_CATALOG",
            "DATABRICKS_PROD_SCHEMA",
            "DATABRICKS_PROD_VOLUME",
        ]:
            with self.subTest(variable=variable):
                self.assertIn(variable, workflow)

        self.assertIn("'lakehouse_demo_dev'", workflow)
        self.assertIn("'lakehouse_demo_prod'", workflow)
        self.assertIn("'lakehouse_demo_dev_files'", workflow)
        self.assertIn("'lakehouse_demo_prod_files'", workflow)
        self.assertIn("github.event.inputs.target == 'prod'", workflow)
        self.assertNotIn("vars.DATABRICKS_SCHEMA", workflow)
        self.assertNotIn("vars.DATABRICKS_VOLUME", workflow)
        self.assertEqual(2, workflow.count("DATABRICKS_DEV_SCHEMA"))
        self.assertEqual(2, workflow.count("DATABRICKS_PROD_SCHEMA"))
        self.assertNotIn("BUNDLE_SCHEMA", workflow.split("\njobs:\n", 1)[0])
        self.assertNotIn("UNITY_CATALOG_VOLUME", workflow.split("\njobs:\n", 1)[0])

        self.assertGreaterEqual(
            workflow.count('--var="schema=${BUNDLE_SCHEMA}"'),
            4,
        )
        self.assertEqual(
            2,
            workflow.count('--bundle-var "schema=${BUNDLE_SCHEMA}"'),
        )
        self.assertEqual(
            2,
            workflow.count(
                '--bundle-var "unity_catalog_volume=${UNITY_CATALOG_VOLUME}"'
            ),
        )
        self.assertIn(
            "dbfs:/Volumes/${BUNDLE_CATALOG}/${BUNDLE_SCHEMA}/${UNITY_CATALOG_VOLUME}",
            workflow,
        )

    def test_deployment_documentation_names_the_isolation_and_evidence_boundary(self):
        documentation = DEPLOYMENT_DOC.read_text(encoding="utf-8")

        self.assertIn("## Target Isolation Contract", documentation)
        self.assertIn("DATABRICKS_DEV_SCHEMA=lakehouse_demo_dev", documentation)
        self.assertIn("DATABRICKS_PROD_SCHEMA=lakehouse_demo_prod", documentation)
        self.assertIn("do not prove the effective Databricks plan", documentation)


if __name__ == "__main__":
    unittest.main()
