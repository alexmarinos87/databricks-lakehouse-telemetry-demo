import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "scripts" / "plan_evidence" / "capture.py"
CORE = ROOT / "scripts" / "plan_evidence" / "core.py"
BRIEF = ROOT / "docs" / "change_briefs" / "attach_plan_review_evidence.md"


class PlanReviewCaptureContractTest(unittest.TestCase):
    def test_shared_capture_runs_review_after_exact_plan_capture(self):
        source = CAPTURE.read_text(encoding="utf-8")
        for token in [
            "PLAN_REVIEW_POLICY",
            "capture_plan_review",
            "databricks-plan-review.json",
            "databricks-plan-review.md",
            'EvidenceError("review", "plan_blocked", review=metadata)',
            'evidence["review"] = capture_plan_review(',
        ]:
            with self.subTest(token=token):
                self.assertIn(token, source)

        plan_position = source.index('evidence["plan"] = capture_bundle_stage')
        review_position = source.index('evidence["review"] = capture_plan_review')
        success_position = source.index(
            'evidence.update({"status": "succeeded"',
            review_position,
        )
        self.assertLess(plan_position, review_position)
        self.assertLess(review_position, success_position)

    def test_review_metadata_is_bounded_and_identity_mode_stays_separate(self):
        source = CAPTURE.read_text(encoding="utf-8")
        self.assertIn('if mode == "plan":', source)
        self.assertIn('"finding_count": len(review["findings"])', source)
        self.assertIn('getattr(error, "review", None)', source)
        self.assertNotIn('review["resources"]', source)
        self.assertNotIn("subprocess", source)

    def test_evidence_error_carries_only_explicit_review_metadata(self):
        source = CORE.read_text(encoding="utf-8")
        self.assertIn("review: Mapping[str, Any] | None = None", source)
        self.assertIn("self.review = dict(review) if review is not None else None", source)

    def test_change_brief_records_shared_workflow_and_side_effect_boundaries(self):
        brief = BRIEF.read_text(encoding="utf-8")
        for token in [
            "owner-only `/databricks-plan dev` command",
            "both manual target plan jobs",
            "writes its full sanitized blocked evidence",
            "failure.category: plan_blocked",
            "Identity-only mode does not invoke the reviewer",
            "does not request another OIDC token",
        ]:
            with self.subTest(token=token):
                self.assertIn(token, brief)


if __name__ == "__main__":
    unittest.main()
