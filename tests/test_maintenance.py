"""Real Git merges with scripted repair, controller validation and independent review."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import common
import maintenance
import run
from pipeline_fixture import PipelineScenario, TraceabilityScenario, seed_repository


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def scenario(self, *, conflict=False):
        seed, bare = self.root / "seed", self.root / "task.git"
        state = seed_repository(seed, bare, files={"shared.txt": "original\n"})
        original = state["base"]
        (seed / "feature.txt").write_text("finished implementation\n")
        if conflict:
            (seed / "shared.txt").write_text("feature version\n")
        common.git(["add", "."], cwd=seed)
        common.git(["commit", "-m", "Finished implementation"], cwd=seed)
        head = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        common.git(
            ["--git-dir", str(bare), "fetch", str(seed), f"{state['branch']}:{state['branch']}"]
        )
        common.git(["checkout", "-b", "main", original], cwd=seed)
        (seed / "shared.txt").write_text("new base\n")
        common.git(["commit", "-am", "Advance base"], cwd=seed)
        base = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        common.git(["--git-dir", str(bare), "fetch", str(seed), "main:main"])
        state.update(base=base, retention_base=original, merge_base=base, commit=head)
        action = {
            "number": 42,
            "head": head,
            "base": base,
            "behind": True,
            "failures": {},
            "feedback": [],
            "reply": None,
        }
        counter = self.root / "checks"
        config = {
            "project": "app",
            "repository": "",
            "repair_attempts": 0,
            "publish_draft": False,
            "repair_pr": action,
            "test_command": f"echo check >> {counter} && test -f feature.txt && test \"$(cat shared.txt)\" = 'new base'",
        }
        artifact = self.root / "artifacts"
        artifact.mkdir()
        return PipelineScenario(self.root), config, {"app": state}, artifact, counter

    def test_clean_merge_skips_agent_but_runs_validation_and_fresh_review(self):
        scenario, config, states, artifact, counter = self.scenario()
        review = Mock(return_value=run.ReviewResult(verdict="PASS", summary="Reviewed merge"))
        with scenario.activate(
            implement=Mock(side_effect=AssertionError("Unexpected agent"))
        ) as hooks:
            with patch.object(run, "review_changes", review):
                result = run.execute_build(
                    [config], "task", "Original approved feature", "", None, False, artifact, states
                )
            hooks.converse.assert_not_called()
        # [utest~im-maintenance-clean-merge~1->req~im-feedback-lifecycle~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(counter.read_text().splitlines(), ["check"])
        review.assert_called_once()
        state = states["app"]
        for parent in (config["repair_pr"]["head"], config["repair_pr"]["base"]):
            common.git(
                [
                    "--git-dir",
                    state["repository"],
                    "merge-base",
                    "--is-ancestor",
                    parent,
                    state["commit"],
                ]
            )
        self.assertEqual(
            common.git(
                ["--git-dir", state["repository"], "show", state["commit"] + ":feature.txt"]
            ).stdout,
            "finished implementation\n",
        )
        receipt = json.loads((artifact / "maintenance-0.json").read_text())
        self.assertFalse(receipt["implementation_agent"])
        progress = json.loads((artifact / "stage-progress.json").read_text())
        self.assertEqual(progress["stages"]["implementation"]["status"], "Reused")

    def test_conflicts_get_a_pointed_prompt_and_preserve_the_full_contract(self):
        scenario, config, states, artifact, counter = self.scenario(conflict=True)
        contract = "Authoritative original feature and accepted decisions"

        def repair(workspace, prompt, *args, **kwargs):
            self.assertTrue(prompt.startswith("Maintain existing PR #42"))
            self.assertNotIn(contract, prompt)
            self.assertIn('"conflicts": ["shared.txt"]', prompt)
            self.assertIn("do not duplicate the full project", prompt)
            self.assertEqual(args[1], "PR maintenance")
            checkout = Path(workspace.working_dir)
            self.assertEqual(
                (checkout.parent.parent / "maintenance-contract.md").read_text(), contract
            )
            self.assertEqual((checkout / "feature.txt").read_text(), "finished implementation\n")
            (checkout / "shared.txt").write_text("new base\n")
            workspace.execute_command("git add shared.txt")
            return run.ImplementationResult(status="IMPLEMENTED", summary="Resolved shared file")

        with scenario.activate(implement=repair) as hooks:
            with patch.object(
                run,
                "review_changes",
                return_value=run.ReviewResult(verdict="PASS", summary="Reviewed"),
            ):
                result = run.execute_build(
                    [config], "task", contract, "", None, False, artifact, states
                )
            hooks.converse.assert_called_once()
        # [utest~im-maintenance-conflict-repair~1->req~im-feedback-lifecycle~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(counter.read_text().splitlines(), ["check"])
        self.assertEqual(states["app"]["maintenance"]["mode"], "conflict_repair")

    def test_failed_validation_never_reviews_or_publishes_clean_merge(self):
        scenario, config, states, artifact, _ = self.scenario()
        config["test_command"] = "exit 1"
        with (
            scenario.activate(
                implement=Mock(side_effect=AssertionError("Unexpected agent"))
            ) as hooks,
            patch.object(run, "review_changes") as review,
            patch.object(run, "publish") as publish,
        ):
            with self.assertRaisesRegex(RuntimeError, "Configured tests failed"):
                run.execute_build(
                    [config], "task", "Original feature", "", None, False, artifact, states
                )
        # [utest~im-maintenance-validation-gate~1->req~im-feedback-lifecycle~1,req~im-publication-gate~1]
        hooks.converse.assert_not_called()
        review.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(
            json.loads((artifact / "result.json").read_text())["phase"], "TESTS_FAILED"
        )

    def test_unresolved_conflicts_cannot_reach_publication(self):
        scenario, config, states, artifact, counter = self.scenario(conflict=True)
        with (
            scenario.activate(
                implement=lambda *a, **k: run.ImplementationResult(
                    status="IMPLEMENTED", summary="Unresolved"
                )
            ),
            patch.object(run, "publish") as publish,
        ):
            with self.assertRaisesRegex(RuntimeError, "Unresolved merge conflicts"):
                run.execute_build(
                    [config], "task", "Original feature", "", None, False, artifact, states
                )
        publish.assert_not_called()
        self.assertFalse(counter.exists())
        self.assertTrue((artifact / "recovery-workspace.txt").exists())

    def test_failed_clean_merge_validation_falls_back_to_a_targeted_repair(self):
        scenario, config, states, artifact, counter = self.scenario()
        config.update(
            repair_attempts=1,
            test_command=f'echo check >> {counter}; test "$(wc -l < {counter})" -gt 1',
        )

        def repair(workspace, prompt, *args, **kwargs):
            self.assertTrue(prompt.startswith("Maintain existing PR #42"))
            self.assertIn("configured tests failed", prompt)
            self.assertIn("Controller test evidence", prompt)
            self.assertNotIn("Original large feature specification", prompt)
            return run.ImplementationResult(
                status="IMPLEMENTED", summary="Repaired validation failure"
            )

        with (
            scenario.activate(implement=repair) as hooks,
            patch.object(
                run,
                "review_changes",
                return_value=run.ReviewResult(verdict="PASS", summary="Reviewed"),
            ) as review,
        ):
            result = run.execute_build(
                [config],
                "task",
                "Original large feature specification",
                "",
                None,
                False,
                artifact,
                states,
            )
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(counter.read_text().splitlines(), ["check", "check"])
        hooks.converse.assert_called_once()
        review.assert_called_once()
        self.assertEqual(
            json.loads((artifact / "maintenance-0.json").read_text())["mode"], "clean_base_update"
        )
        self.assertEqual(
            json.loads((artifact / "maintenance-1.json").read_text())["mode"], "targeted_repair"
        )

    def test_shortcut_excludes_new_scope_failures_retained_changes_and_later_attempts(self):
        action = {"number": 42, "head": "head", "base": "base", "behind": True}
        state = {
            "worktree": "/fixture",
            "maintenance_start": "head",
            "merge_base": "base",
            "merge_exit_code": 0,
        }
        cases = [
            ({key: value}, {}, 0, "")
            for key, value in (
                ("failures", {"unit": "failure"}),
                ("feedback", [{"id": "review:1"}]),
                ("reply", {"answer": "retry"}),
                ("continuation", {"id": "new-scope"}),
                ("behind", False),
            )
        ]
        cases += [
            ({}, {"maintenance_start": "retained-repair"}, 0, ""),
            ({}, {"merge_exit_code": 1}, 0, ""),
            ({}, {}, 1, ""),
            ({}, {}, 0, "conflicted.txt\0"),
        ]
        for index, (changes, state_changes, attempt, conflicts) in enumerate(cases):
            artifact = self.root / str(index)
            artifact.mkdir()
            with (
                self.subTest(
                    changes=changes, state=state_changes, attempt=attempt, conflicts=conflicts
                ),
                patch.object(
                    maintenance, "worker_git", return_value=SimpleNamespace(stdout=conflicts)
                ),
            ):
                record = maintenance.prepare(
                    Mock(),
                    [{"repair_pr": {**action, **changes}}],
                    {"app": {**state, **state_changes}},
                    artifact,
                    attempt,
                )
                # [utest~im-maintenance-shortcut-guards~1->req~im-feedback-lifecycle~1]
                self.assertTrue(record["implementation_agent"])

    def test_traceability_maintenance_runs_only_one_controller_check(self):
        scenario = TraceabilityScenario(self, self.root)
        head = scenario.states["pilot"]["base"]
        scenario.config["repair_pr"] = {
            "number": 42,
            "head": head,
            "base": head,
            "behind": False,
            "failures": {"unit": "failure"},
        }
        result = scenario.invoke()
        # [utest~im-maintenance-single-validation~1->req~im-feedback-lifecycle~1,req~im-trace-source-gate~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(scenario.attempts), 1)
        self.assertNotIn("-m traceability.invocation", scenario.attempts[0])
        self.assertIn("Maintenance validation is assigned to the controller", scenario.attempts[0])
        index = json.loads(
            (scenario.artifact / "pilot/traceability-invocations-0/index.json").read_text()
        )
        self.assertEqual(index["feedback_iterations"], 0)
        self.assertEqual(index["controller_checks"], 1)
        self.assertEqual(len(scenario.review_prompts), 1)
