import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
BRONZE_NOTEBOOK = REPO_ROOT / "notebooks" / "01_bronze_ingest.py"
IDENTITY_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "ingestion_identity.py"
SPARK_IDENTITY_MODULE = (
    REPO_ROOT / "src" / "lakehouse_demo" / "spark_ingestion_identity.py"
)
PLAN_SCRIPT = REPO_ROOT / "scripts" / "plan_ingestion_upload.py"
UPLOAD_SCRIPT = REPO_ROOT / "scripts" / "upload_ingestion_plan.py"
SETUP_DOC = REPO_ROOT / "docs" / "setup.md"
CHANGE_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "immutable_ingestion_replay.md"
)


class IngestionReplayContractTest(unittest.TestCase):
    def test_deployment_uses_explicit_dataset_mode_and_replay_inputs(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("demo_dataset:", workflow)
        self.assertIn("ingestion_mode:", workflow)
        self.assertIn("backfill_id:", workflow)
        self.assertIn("incremental", workflow)
        self.assertIn("backfill", workflow)
        self.assertIn("increment-2026-04-03", workflow)
        self.assertGreaterEqual(workflow.count("scripts/plan_ingestion_upload.py"), 2)
        self.assertGreaterEqual(workflow.count("scripts/upload_ingestion_plan.py"), 2)
        self.assertNotIn("--overwrite", workflow)
        self.assertNotIn("databricks fs rm", workflow)
        self.assertNotIn("dbutils.fs.rm", workflow)

    def test_planner_and_uploader_prohibit_overwrite_and_checkpoint_reset(self):
        identity = IDENTITY_MODULE.read_text(encoding="utf-8")
        uploader = UPLOAD_SCRIPT.read_text(encoding="utf-8")
        planner = PLAN_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('CHECKPOINT_POLICY = "reuse_existing_checkpoint"', identity)
        self.assertIn('"allow_overwrites": False', identity)
        self.assertIn("MODE_BACKFILL", planner)
        self.assertIn("--replay-id", planner)
        self.assertIn('"databricks",\n            "fs",\n            "cp"', uploader)
        self.assertNotIn('"--overwrite"', uploader)
        self.assertNotIn('"rm"', uploader)
        self.assertIn('"cat"', uploader)
        self.assertIn("Uploaded landing object did not match", uploader)

    def test_bronze_preflights_and_records_immutable_source_identity(self):
        notebook = BRONZE_NOTEBOOK.read_text(encoding="utf-8")
        spark_identity = SPARK_IDENTITY_MODULE.read_text(encoding="utf-8")

        self.assertIn("parse_object_name", notebook)
        self.assertIn("with_ingestion_identity", notebook)
        self.assertIn("_list_landing_files", notebook)
        self.assertIn("landing objects violate the immutable identity contract", notebook)
        self.assertIn('option("cloudFiles.allowOverwrites", False)', notebook)
        self.assertIn("_source_object_name", spark_identity)
        self.assertIn("_ingestion_mode", spark_identity)
        self.assertIn("_replay_id", spark_identity)
        self.assertIn("_source_content_sha256", spark_identity)
        self.assertIn("_source_identity_valid", spark_identity)
        self.assertNotIn("checkpoint_path", notebook[notebook.index("def _list_landing_files"):notebook.index("raw_schema =")])
        self.assertNotIn("dbutils.fs.rm", notebook)

    def test_documentation_requires_checkpoint_reuse_and_explicit_backfill(self):
        setup = SETUP_DOC.read_text(encoding="utf-8")
        brief = CHANGE_BRIEF.read_text(encoding="utf-8")

        for source in (setup, brief):
            with self.subTest(source=source[:20]):
                self.assertIn("reuse", source.lower())
                self.assertIn("checkpoint", source.lower())
                self.assertIn("backfill", source.lower())
                self.assertIn("replay ID", source)
        self.assertIn("Do not delete", setup)
        self.assertIn("same content", setup.lower())
        self.assertIn("different content", setup.lower())
        self.assertIn("no implicit checkpoint deletion", brief.lower())


if __name__ == "__main__":
    unittest.main()
