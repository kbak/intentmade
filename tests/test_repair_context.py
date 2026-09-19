"""Real source and artifact identities guard bounded repair memory."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common
import input_artifacts as inputs
import repair_context as repair

RUNTIME = {"image_id": "sha256:worker", "workflow_sha256": "workflow", "model": "model/effort"}


class RepairContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "source"
        common.git(["init", "-b", "task", str(source)])
        common.git(["config", "user.name", "Fixture"], cwd=source)
        common.git(["config", "user.email", "fixture@localhost"], cwd=source)
        (source / "queue.py").write_text("value = 1\n")
        common.git(["add", "."], cwd=source)
        common.git(["commit", "-m", "baseline"], cwd=source)
        self.base = common.git(["rev-parse", "HEAD"], cwd=source).stdout.strip()
        self.repo = self.root / "task.git"
        common.git(["clone", "--bare", str(source), str(self.repo)])
        self.configs = [
            {"project": "queue", "repository": "owner/queue", "test_command": "python -m unittest"}
        ]
        self.states = {
            "queue": {
                "repository": str(self.repo),
                "branch": "task",
                "base": self.base,
                "summary": "Implemented queue transition",
            }
        }
        self.contract = "Approved contract\n" + ("Preserve the existing promise.\n" * 4000)
        self.artifact = self.root / "attempt-one"
        (self.artifact / "queue").mkdir(parents=True)
        repair.initialize(self.artifact, self.contract)
        (self.artifact / "queue/tests-0.log").write_text(
            "AssertionError: expected delay 10, got 0\n"
        )
        for target, attribute, value in [
            (repair, "runtime", lambda: dict(RUNTIME)),
            (inputs, "DATA", self.root / "data"),
        ]:
            context = patch.object(target, attribute, value)
            context.start()
            self.addCleanup(context.stop)
        repair.retain(
            self.configs,
            self.states,
            "task",
            self.contract,
            self.artifact,
            0,
            "TESTS_FAILED",
            "AssertionError: expected delay 10, got 0",
        )

    def test_restart_reuses_exact_context_and_stages_complete_contract_and_logs(self):
        second = self.root / "new-native-run"
        second.mkdir()
        with repair.staged(self.configs, self.states, "task", self.contract, second, 0) as (
            selected,
            decision,
        ):
            self.assertIsNotNone(selected)
            prompt = repair.prompt(selected, decision, self.contract + "\nold repeated narrative")
            self.assertIn("expected delay 10", prompt)
            self.assertNotIn("old repeated narrative", prompt)
            self.assertLess(len(prompt), len(self.contract) // 10)
            frozen = inputs.CURRENT.get()
            for reference, path in selected["paths"].items():
                self.assertEqual(
                    (Path(frozen["directory"]) / Path(path).name).read_bytes(),
                    (self.artifact / reference).read_bytes(),
                )
            self.assertEqual(decision["strategy"], "verified_bounded_context")
        self.assertIsNone(
            inputs.CURRENT.get(), "Review must not inherit implementation repair inputs"
        )
        self.assertEqual(
            json.loads((second / "continuation-selection-0.json").read_text())["strategy"],
            "verified_bounded_context",
        )
        self.assertTrue((self.artifact / "continuation-0.json").is_file())

    def test_stale_contract_base_policy_candidate_and_artifact_are_not_reused(self):
        for changed in ("contract", "base", "policy", "candidate", "artifact"):
            with self.subTest(changed=changed):
                states = json.loads(json.dumps(self.states))
                configs = json.loads(json.dumps(self.configs))
                contract = self.contract
                if changed == "contract":
                    contract += "changed promise"
                if changed == "base":
                    states["queue"]["base"] = "different"
                if changed == "policy":
                    configs[0]["test_command"] = "true"
                if changed == "candidate":
                    tree = common.git(
                        ["--git-dir", str(self.repo), "rev-parse", self.base + "^{tree}"]
                    ).stdout.strip()
                    other = common.git(
                        [
                            "--git-dir",
                            str(self.repo),
                            "-c",
                            "user.name=Fixture",
                            "-c",
                            "user.email=fixture@localhost",
                            "commit-tree",
                            tree,
                            "-p",
                            self.base,
                            "-m",
                            "Different candidate",
                        ]
                    ).stdout.strip()
                    common.git(
                        ["--git-dir", str(self.repo), "update-ref", "refs/heads/other", other]
                    )
                    states["queue"]["branch"] = "other"
                if changed == "artifact":
                    (self.artifact / "queue/tests-0.log").write_text("forged pass")
                selected, reason = repair.previous(configs, states, "task", contract)
                self.assertIsNone(selected)
                self.assertIn("context_rejected", reason)

    def test_runtime_change_falls_back_without_weakening_current_contract(self):
        second = self.root / "next"
        second.mkdir()
        with repair.staged(self.configs, self.states, "task", self.contract, second, 1) as (
            selected,
            decision,
        ):
            with patch.object(
                repair, "runtime", return_value={**RUNTIME, "image_id": "sha256:other"}
            ):
                self.assertEqual(repair.prompt(selected, decision, self.contract), self.contract)
                self.assertEqual(decision["reason"], "runtime_identity_unknown_or_changed")

    def test_records_are_immutable_and_success_is_not_a_repair(self):
        before = (self.artifact / "continuation-0.json").read_bytes()
        repair.retain(
            self.configs,
            self.states,
            "task",
            self.contract,
            self.artifact,
            0,
            "PASSED",
            "overwrite",
        )
        self.assertEqual((self.artifact / "continuation-0.json").read_bytes(), before)
        repair.retain(
            self.configs, self.states, "task", self.contract, self.artifact, 1, "PASSED", ""
        )
        self.assertEqual(
            repair.previous(self.configs, self.states, "task", self.contract),
            (None, "previous_attempt_not_repairable"),
        )

    def test_real_pipeline_repairs_with_verified_context_and_fresh_review(self):
        from test_traceability import TraceabilityPipelineTests

        fixture = TraceabilityPipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.config["repair_attempts"] = 1

        def edit(root, attempt):
            path = root / "session.py"
            if attempt == 2:
                self.assertIn(
                    "complete authoritative contract at /factory-inputs/", fixture.attempts[-1]
                )
                self.assertIn("AssertionError", fixture.attempts[-1])
                frozen = inputs.CURRENT.get()
                self.assertIsNotNone(frozen)
                self.assertTrue(
                    any(
                        p.read_text() == fixture.request
                        for p in Path(frozen["directory"]).iterdir()
                    )
                )
            path.write_text(
                path.read_text().replace("1800", "3600")
                if attempt == 1
                else path.read_text().replace("3600", "1800")
            )

        output = fixture.invoke(edit)
        self.assertEqual(output["status"], "PASSED")
        self.assertEqual(len(fixture.attempts), 2)
        self.assertEqual(len(fixture.review_prompts), 1)
        self.assertNotIn("source-bound repair record", fixture.review_prompts[0])
        self.assertEqual(
            json.loads((fixture.artifact / "continuation-selection-1.json").read_text())[
                "strategy"
            ],
            "verified_bounded_context",
        )
