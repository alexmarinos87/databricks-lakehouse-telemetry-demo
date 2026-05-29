import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRONZE_NOTEBOOK = REPO_ROOT / "notebooks" / "01_bronze_ingest.py"
BUNDLE = REPO_ROOT / "databricks.yml"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
SETUP_DOC = REPO_ROOT / "docs" / "setup.md"


class UnityCatalogVolumeIngestionContractTest(unittest.TestCase):
    def test_bronze_notebook_accepts_volume_widgets(self):
        notebook_source = BRONZE_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn('dbutils.widgets.text("unity_catalog_volume", "")', notebook_source)
        self.assertIn('dbutils.widgets.text("create_unity_catalog_volume", "true")', notebook_source)
        self.assertIn('dbutils.widgets.text("volume_source_path", "raw_machine_events")', notebook_source)
        self.assertIn('dbutils.widgets.text("volume_checkpoint_path"', notebook_source)
        self.assertIn('dbutils.widgets.text("volume_schema_location"', notebook_source)
        self.assertIn("CREATE VOLUME IF NOT EXISTS", notebook_source)
        self.assertIn("quote_sql_identifier", notebook_source)

    def test_bundle_exposes_volume_variables(self):
        bundle_source = BUNDLE.read_text(encoding="utf-8")

        self.assertIn("unity_catalog_volume:", bundle_source)
        self.assertIn("create_unity_catalog_volume:", bundle_source)
        self.assertIn("volume_source_path:", bundle_source)
        self.assertIn("volume_checkpoint_path:", bundle_source)
        self.assertIn("volume_schema_location:", bundle_source)

    def test_workflow_passes_volume_parameters_to_bronze_task(self):
        workflow_source = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("unity_catalog_volume: ${var.unity_catalog_volume}", workflow_source)
        self.assertIn("create_unity_catalog_volume: ${var.create_unity_catalog_volume}", workflow_source)
        self.assertIn("volume_source_path: ${var.volume_source_path}", workflow_source)
        self.assertIn("volume_checkpoint_path: ${var.volume_checkpoint_path}", workflow_source)
        self.assertIn("volume_schema_location: ${var.volume_schema_location}", workflow_source)

    def test_setup_docs_include_volume_upload_path(self):
        setup_source = SETUP_DOC.read_text(encoding="utf-8")

        self.assertIn("Unity Catalog Volume Ingestion", setup_source)
        self.assertIn("/Volumes/<catalog>/<schema>/<unity_catalog_volume>", setup_source)
        self.assertIn("dbfs:/Volumes/main/lakehouse_demo/lakehouse_demo_files", setup_source)


if __name__ == "__main__":
    unittest.main()
