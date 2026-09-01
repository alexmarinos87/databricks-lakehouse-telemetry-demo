import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "artifact-compatibility.yml"

CHECKOUT_SHA = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
UPLOAD_SHA = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_V8_SHA = (
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
)


class ArtifactCompatibilityWorkflowTest(unittest.TestCase):
    def source(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_runs_for_pull_requests_and_accepted_main(self):
        source = self.source()
        trigger = source.split("\npermissions:\n", 1)[0]

        self.assertTrue(WORKFLOW.is_file())
        self.assertIn("pull_request:", trigger)
        self.assertIn("push:", trigger)
        self.assertIn("- main", trigger)
        self.assertNotIn("workflow_dispatch:", trigger)
        self.assertNotIn("schedule:", trigger)

    def test_workflow_is_least_privilege_bounded_and_non_concurrent(self):
        source = self.source()

        self.assertIn("permissions:\n  contents: read", source)
        self.assertNotIn("id-token: write", source)
        self.assertNotIn("actions: write", source)
        self.assertNotIn("contents: write", source)
        self.assertIn("runs-on: ubuntu-24.04", source)
        self.assertIn("timeout-minutes: 5", source)
        self.assertIn("cancel-in-progress: true", source)
        self.assertIn(
            "group: artifact-compatibility-${{ github.workflow }}-${{ github.ref }}",
            source,
        )

    def test_all_actions_are_immutable_and_candidate_download_is_v8(self):
        source = self.source()

        self.assertEqual(1, source.count(CHECKOUT_SHA))
        self.assertEqual(1, source.count(UPLOAD_SHA))
        self.assertEqual(1, source.count(DOWNLOAD_V8_SHA))
        self.assertNotIn("actions/checkout@v", source)
        self.assertNotIn("actions/upload-artifact@v", source)
        self.assertNotIn("actions/download-artifact@v", source)
        self.assertIn("Download synthetic evidence with candidate v8 action", source)

    def test_fixture_is_synthetic_and_bound_to_workflow_provenance(self):
        source = self.source()

        for token in (
            '"artifact_kind": "synthetic_review_evidence"',
            'os.environ["GITHUB_REPOSITORY"]',
            'os.environ["GITHUB_RUN_ATTEMPT"]',
            'os.environ["GITHUB_RUN_ID"]',
            'os.environ["GITHUB_SHA"]',
            '"schema_version": 1',
            "contains no Databricks plan, credential, business data",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

        self.assertNotIn("DATABRICKS_HOST", source)
        self.assertNotIn("DATABRICKS_CLIENT_ID", source)
        self.assertNotIn("DATABRICKS_CLIENT_SECRET", source)
        self.assertNotIn("secrets.", source)

    def test_upload_is_unique_non_overwriting_and_short_lived(self):
        source = self.source()

        self.assertIn(
            "ARTIFACT_NAME: artifact-compatibility-${{ github.run_id }}-"
            "${{ github.run_attempt }}",
            source,
        )
        self.assertEqual(2, source.count("name: ${{ env.ARTIFACT_NAME }}"))
        self.assertIn("path: artifact-input", source)
        self.assertIn("if-no-files-found: error", source)
        self.assertIn("retention-days: 1", source)
        self.assertIn("compression-level: 6", source)
        self.assertIn("overwrite: false", source)
        self.assertIn("include-hidden-files: false", source)

    def test_download_verification_fails_closed_on_shape_or_byte_drift(self):
        source = self.source()

        self.assertIn("path: artifact-output", source)
        self.assertIn("digest-mismatch: error", source)
        self.assertIn("Downloaded artifact contains a symbolic link", source)
        self.assertIn("Downloaded artifact contains a non-regular entry", source)
        self.assertIn("diff -u expected-files.txt actual-files.txt", source)
        self.assertIn("sha256sum --check manifest.sha256", source)
        self.assertIn("Downloaded artifact provenance does not match this run", source)

        for expected_file in (
            "manifest.sha256",
            "review-evidence/review.json",
            "review-evidence/summary.md",
        ):
            with self.subTest(expected_file=expected_file):
                self.assertIn(expected_file, source)

        self.assertNotIn("skip-decompress: true", source)
        self.assertNotIn("digest-mismatch: ignore", source)
        self.assertNotIn("digest-mismatch: warn", source)


if __name__ == "__main__":
    unittest.main()
