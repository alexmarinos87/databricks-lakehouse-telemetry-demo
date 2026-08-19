import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_repo_contracts  # noqa: E402
import generate_review_package  # noqa: E402


class DeliveryWorkflowContractTest(unittest.TestCase):
    @staticmethod
    def run_git(root, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def initialize_repository(self, root):
        self.run_git(root, "init", "--initial-branch=main")
        self.run_git(root, "config", "user.name", "Delivery Test")
        self.run_git(root, "config", "user.email", "delivery-test@example.invalid")
        (root / "README.md").write_text("baseline\n", encoding="utf-8")
        self.run_git(root, "add", "README.md")
        self.run_git(root, "commit", "-m", "baseline")

    def test_agent_contract_requires_human_acceptance(self):
        instructions = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("Never merge or deploy", instructions)
        self.assertIn("Another agent's approval", instructions)
        self.assertIn("scripts/run_acceptance_checks.sh", instructions)
        self.assertIn("adversarial production review", instructions)

    def test_pull_request_template_keeps_acceptance_human_only(self):
        template = (REPO_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")

        self.assertIn("Human Acceptance — Human Reviewer Only", template)
        self.assertIn("Decision: **Pending**", template)
        self.assertIn("Failure, Recovery And Rollback", template)
        self.assertIn("Security, Permissions And Cost", template)

    def test_risk_register_local_evidence_links_resolve(self):
        register_path = REPO_ROOT / "docs" / "engineering_risk_register.md"
        register = register_path.read_text(encoding="utf-8")
        linked_paths = re.findall(r"\]\((\.\./[^)#]+)(?:#[^)]+)?\)", register)
        missing = [
            relative
            for relative in linked_paths
            if not (register_path.parent / relative).resolve().exists()
        ]

        self.assertGreater(len(linked_paths), 10)
        self.assertEqual([], missing)

    def test_reporting_manifest_contract_is_valid(self):
        errors, asset_count = check_repo_contracts.validate_reporting_manifest(REPO_ROOT)

        self.assertEqual([], errors)
        self.assertGreater(asset_count, 0)

    def test_exported_tree_scan_falls_back_when_git_is_not_installed(self):
        token = "ghp_" + ("c" * 36)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidate.txt").write_text(f"token={token}\n", encoding="utf-8")
            with mock.patch.object(
                check_repo_contracts.subprocess,
                "run",
                side_effect=FileNotFoundError("git"),
            ):
                files = check_repo_contracts.repository_files(root)
                errors = check_repo_contracts.scan_for_secrets(root, files)

        self.assertEqual([Path("candidate.txt")], files)
        self.assertEqual(1, len(errors))
        self.assertIn("possible GitHub token in candidate.txt:1", errors[0])
        self.assertNotIn(token, errors[0])

    def test_repository_scan_fails_closed_when_git_is_not_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.initialize_repository(root)
            with mock.patch.object(
                check_repo_contracts.subprocess,
                "run",
                side_effect=FileNotFoundError("git"),
            ):
                with self.assertRaises(check_repo_contracts.CandidateFileError):
                    check_repo_contracts.uses_git_index(root)

    def test_repository_scan_fails_closed_when_git_metadata_is_unreadable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.initialize_repository(root)
            failed_probe = subprocess.CompletedProcess(
                args=["git", "rev-parse"],
                returncode=128,
                stdout="",
                stderr="repository metadata unavailable",
            )
            with mock.patch.object(
                check_repo_contracts.subprocess,
                "run",
                return_value=failed_probe,
            ):
                with self.assertRaises(check_repo_contracts.CandidateFileError):
                    check_repo_contracts.uses_git_index(root)

    def test_secret_scan_reports_location_without_echoing_value(self):
        token = "ghp_" + ("a" * 36)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidate.txt").write_text(f"token={token}\n", encoding="utf-8")
            errors = check_repo_contracts.scan_for_secrets(
                root, [Path("candidate.txt")], use_index=False
            )

        self.assertEqual(1, len(errors))
        self.assertIn("candidate.txt:1", errors[0])
        self.assertNotIn(token, errors[0])

    def test_secret_scan_reads_staged_bytes_not_safe_worktree_replacement(self):
        token = "ghp_" + ("b" * 36)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.initialize_repository(root)
            candidate = root / "candidate.txt"
            candidate.write_text(f"token={token}\n", encoding="utf-8")
            self.run_git(root, "add", "candidate.txt")
            candidate.write_text("safe worktree replacement\n", encoding="utf-8")

            errors = check_repo_contracts.scan_for_secrets(root, [Path("candidate.txt")])
            consistency_errors = check_repo_contracts.validate_candidate_worktree_consistency(root)

        self.assertEqual(1, len(errors))
        self.assertIn("possible GitHub token in candidate.txt:1", errors[0])
        self.assertNotIn(token, errors[0])
        self.assertEqual(1, len(consistency_errors))
        self.assertIn("candidate.txt", consistency_errors[0])

    def test_consistency_check_rejects_dirty_committed_candidate_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.initialize_repository(root)
            self.run_git(root, "switch", "-c", "agent/dirty-candidate")
            candidate = root / "candidate.py"
            candidate.write_text("VALUE = 'committed'\n", encoding="utf-8")
            self.run_git(root, "add", "candidate.py")
            self.run_git(root, "commit", "-m", "add candidate")
            candidate.write_text("VALUE = 'different worktree bytes'\n", encoding="utf-8")

            errors = check_repo_contracts.validate_candidate_worktree_consistency(
                root, "main"
            )

        self.assertEqual(1, len(errors))
        self.assertIn("candidate.py", errors[0])

    def test_secret_scan_rejects_symlinks_and_oversized_text(self):
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            root = container / "export"
            root.mkdir()
            outside = container / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            (root / "linked.txt").symlink_to(outside)
            (root / "large.txt").write_bytes(
                b"x" * (check_repo_contracts.MAX_TEXT_FILE_BYTES + 1)
            )

            errors = check_repo_contracts.scan_for_secrets(
                root,
                [Path("linked.txt"), Path("large.txt")],
                use_index=False,
            )

        self.assertTrue(any("symbolic links are not allowed" in error for error in errors))
        self.assertTrue(any("inspection limit" in error for error in errors))

    def test_json_and_manifest_reject_invalid_candidate_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.json").write_text("{not json}\n", encoding="utf-8")
            manifest_path = root / "sql" / "reporting_assets" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "display_name": "Unsafe",
                            "description": "Path traversal fixture",
                            "file": "../../outside.sql",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            json_errors = check_repo_contracts.validate_json_files(
                root, [Path("bad.json")], use_index=False
            )
            manifest_errors, _ = check_repo_contracts.validate_reporting_manifest(
                root, use_index=False
            )

        self.assertEqual(1, len(json_errors))
        self.assertTrue(any("unsafe reporting file path" in error for error in manifest_errors))

    def test_review_package_classifies_and_parses_diff_stats(self):
        files = generate_review_package.parse_numstat(
            "12\t3\tnotebooks/08_example.py\n1\t0\tdocs/example.md"
        )

        self.assertEqual("runtime/data flow", files[0]["category"])
        self.assertEqual(12, files[0]["additions"])
        self.assertEqual("governance/docs", files[1]["category"])

    def test_review_package_covers_staged_and_committed_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.initialize_repository(root)
            merge_base = self.run_git(root, "rev-parse", "HEAD")
            self.run_git(root, "switch", "-c", "agent/review-test")
            (root / "scripts").mkdir()
            (root / "scripts" / "tool.py").write_text("print('candidate')\n", encoding="utf-8")
            self.run_git(root, "add", "scripts/tool.py")
            (root / "unrelated.txt").write_text("not staged\n", encoding="utf-8")

            staged_package = generate_review_package.render_package(
                "main", "HEAD", "HEAD", "passed", repo_root=root
            )

            self.assertIn("staged index (HEAD plus 1 staged path(s))", staged_package)
            self.assertIn("| Scope | 1 files, +1/-0 |", staged_package)
            self.assertIn("`scripts/tool.py`", staged_package)
            self.assertIn("`?? unrelated.txt`", staged_package)

            self.run_git(root, "commit", "-m", "add review fixture")
            candidate_sha = self.run_git(root, "rev-parse", "HEAD")
            self.run_git(root, "switch", "main")
            (root / "main.txt").write_text("base advanced\n", encoding="utf-8")
            self.run_git(root, "add", "main.txt")
            self.run_git(root, "commit", "-m", "advance base")
            base_tip = self.run_git(root, "rev-parse", "HEAD")
            self.run_git(root, "switch", "agent/review-test")

            committed_package = generate_review_package.render_package(
                "main", "HEAD", "main", "passed", repo_root=root
            )

            self.assertIn(f"| Base ref tip | `main` at `{base_tip}` |", committed_package)
            self.assertIn(f"| Merge base | `{merge_base}` |", committed_package)
            self.assertIn(f"| Candidate head | `HEAD` at `{candidate_sha}` |", committed_package)
            self.assertIn(f"| Tested checkout | `main` at `{base_tip}` |", committed_package)
            self.assertIn("| Comparison target | committed candidate head |", committed_package)

            with self.assertRaises(subprocess.CalledProcessError):
                generate_review_package.render_package(
                    "missing-base", "HEAD", "HEAD", "passed", repo_root=root
                )

    def test_local_checks_include_repository_contracts(self):
        checks = (REPO_ROOT / "scripts" / "run_local_checks.sh").read_text(encoding="utf-8")
        acceptance = (REPO_ROOT / "scripts" / "run_acceptance_checks.sh").read_text(encoding="utf-8")

        self.assertIn("scripts/check_repo_contracts.py", checks)
        self.assertIn("CONTRACT_BASE_REF", checks)
        self.assertIn("git merge-base", acceptance)
        self.assertIn("git checkout-index", acceptance)
        self.assertIn("mktemp -d", acceptance)
        self.assertIn("git diff --check", acceptance)


if __name__ == "__main__":
    unittest.main()
