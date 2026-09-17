"""Real Git regressions for review identity when remote-tracking refs move."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_event_base", ROOT / "scripts/generate_review_package.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class ReviewEventBaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Event base test")
        self.git("config", "user.email", "event-base@example.invalid")
        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "base.txt")
        self.git("commit", "-qm", "Synthetic base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "feature")
        (self.root / "feature.txt").write_text("feature\n", encoding="utf-8")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "Synthetic candidate")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "Synthetic tested merge", "feature")
        self.tested = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.base)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True,
            stderr=subprocess.DEVNULL, timeout=10,
        ).strip()

    def render(self, base=None, status="success"):
        return subject.render_package(self.base if base is None else base,
                                      self.head, self.tested, status, self.root)

    def test_event_base_preserves_diff_after_tracking_branch_advances(self):
        self.git("update-ref", "refs/remotes/origin/main", self.tested)
        moving = self.render("origin/main")
        anchored = self.render()
        # The old wiring really can hide the reviewed candidate after base movement.
        self.assertIn("| Scope | 0 files, +0/-0 |", moving)
        self.assertIn("| Scope | 1 files, +1/-0 |", anchored)
        self.assertIn(f"| Base ref tip | `{self.base}` at `{self.base}` |", anchored)
        self.assertIn("| `feature.txt` | other | 1 | 0 |", anchored)

    def test_event_base_survives_deleted_tracking_ref(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.render("origin/main")
        self.assertIn("| Scope | 1 files, +1/-0 |", self.render())

    def test_unavailable_event_base_does_not_fall_back_to_tracking_ref(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.render("0" * 40)

    def test_candidate_and_tested_merge_remain_distinct(self):
        package = self.render()
        self.assertEqual(3, len({self.base, self.head, self.tested}))
        self.assertIn(f"| Candidate head | `{self.head}` at `{self.head}` |", package)
        self.assertIn(f"| Tested checkout | `{self.tested}` at `{self.tested}` |", package)
        self.assertIn("Decision: **Pending**", package)

    def test_failed_validation_status_is_preserved(self):
        self.assertIn("| Validation status supplied by caller | failure |", self.render(status="failure"))

    def test_ci_review_uses_event_sha_without_weakening_failure_reporting(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        review = text.split("      - name: Generate review package\n", 1)[1].split(
            "      - name: Publish review package summary\n", 1
        )[0]
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.sha }}", review)
        self.assertNotIn("origin/", review)
        self.assertIn("CANDIDATE_HEAD: ${{ github.event.pull_request.head.sha }}", review)
        self.assertIn("TESTED_CHECKOUT: ${{ github.sha }}", review)
        self.assertIn("VALIDATION_STATUS: ${{ job.status }}", review)
        self.assertIn("!cancelled() && github.event_name == 'pull_request'", review)
        self.assertIn('--base "${BASE_REF}"', review)
        self.assertNotIn("continue-on-error", text)
        self.assertIn("needs: validate", text)


if __name__ == "__main__":
    unittest.main()
