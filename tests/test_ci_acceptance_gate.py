"""Exercise the real acceptance shell with isolated check fixtures, plus CI wiring."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CIAcceptanceGateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        (self.root / "scripts").mkdir(parents=True)
        self.evidence = Path(self.temporary.name) / "evidence.json"
        shutil.copyfile(ROOT / "scripts/run_acceptance_checks.sh",
                        self.root / "scripts/run_acceptance_checks.sh")
        # Check fixtures isolate shell orchestration; real checks run in the CI gate.
        (self.root / "scripts/check_repo_contracts.py").write_text(
            '#!/usr/bin/env python3\nimport os, sys\nsys.exit(int(os.environ.get("FAIL_CONTRACT", "0")))\n'
        )
        (self.root / "scripts/run_local_checks.sh").write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n'
            'if os.environ.get("FAIL_EXPORTED") == "1": sys.exit(1)\n'
            'Path(os.environ["TEST_EVIDENCE"]).write_text(json.dumps({\n'
            '    "candidate": Path("candidate.txt").read_text(),\n'
            '    "untracked_present": Path("untracked.txt").exists(),\n'
            '    "git_present": Path(".git").exists(),\n'
            '}))\n'
        )
        for script in (self.root / "scripts").iterdir():
            script.chmod(0o755)
        (self.root / "candidate.txt").write_text("base\n")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Acceptance test")
        self.git("config", "user.email", "acceptance@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *arguments],
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )

    def run_gate(self, **overrides):
        environment = {**os.environ, "BASE_REF": self.base, "TEST_EVIDENCE": str(self.evidence),
                       "FAIL_CONTRACT": "0", "FAIL_EXPORTED": "0", **overrides}
        return subprocess.run(
            ["bash", "scripts/run_acceptance_checks.sh"], cwd=self.root, env=environment,
            capture_output=True, text=True, timeout=20,
        )

    def test_success_executes_exported_checks(self):
        result = self.run_gate()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"candidate": "base\n", "untracked_present": False, "git_present": False},
                         json.loads(self.evidence.read_text()))

    def test_contract_failure_prevents_exported_checks(self):
        self.assertNotEqual(0, self.run_gate(FAIL_CONTRACT="1").returncode)
        self.assertFalse(self.evidence.exists())

    def test_exported_check_failure_is_not_hidden(self):
        self.assertNotEqual(0, self.run_gate(FAIL_EXPORTED="1").returncode)
        self.assertFalse(self.evidence.exists())

    def test_unavailable_base_fails_before_checks(self):
        self.assertNotEqual(0, self.run_gate(BASE_REF="0" * 40).returncode)
        self.assertFalse(self.evidence.exists())

    def test_exact_index_excludes_unstaged_and_untracked_bytes(self):
        (self.root / "candidate.txt").write_text("staged\n")
        self.git("add", "candidate.txt")
        (self.root / "candidate.txt").write_text("unstaged\n")
        (self.root / "untracked.txt").write_text("unrelated\n")
        result = self.run_gate()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("staged\n", json.loads(self.evidence.read_text())["candidate"])
        self.assertFalse(json.loads(self.evidence.read_text())["untracked_present"])
        self.assertEqual("unstaged\n", (self.root / "candidate.txt").read_text())
        self.assertEqual("unrelated\n", (self.root / "untracked.txt").read_text())

    def test_staged_whitespace_failure_is_not_hidden(self):
        (self.root / "candidate.txt").write_text("bad trailing space \n")
        self.git("add", "candidate.txt")
        self.assertNotEqual(0, self.run_gate().returncode)
        self.assertTrue(self.evidence.exists())

    def test_ci_gate_precedes_review_and_blocks_dependent_publication(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        validation, publication = text.split("  portfolio-evidence:", 1)
        start = validation.index("      - name: Run exact-index acceptance checks\n")
        end = validation.index("      - name: Generate review package\n")
        gate = validation[start:end]
        self.assertLess(validation.index("run: docker run --rm lakehouse-demo-ci"), start)
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.sha || github.event.before }}", gate)
        self.assertIn("run: scripts/run_acceptance_checks.sh", gate)
        self.assertNotIn("if:", gate)
        self.assertNotIn("continue-on-error", validation)
        self.assertIn("needs: validate", publication)
        self.assertIn("CI_VALIDATION_RESULT: ${{ needs.validate.result }}", publication)
        self.assertNotIn("always()", publication)
        self.assertNotIn("continue-on-error", publication)


if __name__ == "__main__":
    unittest.main()
