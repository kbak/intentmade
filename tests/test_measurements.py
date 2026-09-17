"""Measurements preserve attempt history without granting assurance or changing gates."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent
import measurements
import reporting
import run


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def record(self):
        return json.loads((self.root / "metrics.json").read_text())

    def test_repaired_build_retains_both_checks_and_source_versions(self):
        config = {
            "project": "app",
            "repository": "org/app",
            "repair_attempts": 1,
            "publish_draft": False,
        }
        states = {"app": {"base": "base", "branch": "branch", "repository": "retained"}}

        def implement(*args):
            attempt = args[-1]
            states["app"]["commit"] = f"candidate-{attempt}"
            return {"app": 1 if attempt == 0 else 0}, []

        with (
            patch.object(run, "implementation_attempt", side_effect=implement),
            patch.object(
                run,
                "review_changes",
                return_value=run.ReviewResult(verdict="PASS", summary="Reviewed"),
            ),
        ):
            result = run.execute_build([config], "task", "spec", "", None, False, self.root, states)
        record = self.record()
        self.assertEqual(result["metrics"], str(self.root / "metrics.json"))
        self.assertEqual(record["status"], "PASSED")
        self.assertEqual([a["status"] for a in record["attempts"]], ["TESTS_FAILED", "PASSED"])
        self.assertEqual([a["reason"] for a in record["attempts"]], ["initial", "tests"])
        for index, attempt in enumerate(record["attempts"]):
            self.assertEqual(
                attempt["outcome"]["repositories"]["app"]["commit"], f"candidate-{index}"
            )
            self.assertIsNotNone(attempt["completed_at"])
        self.assertNotIn("review", record["attempts"][0])
        self.assertEqual(record["attempts"][1]["review"]["verdict"], "PASS")
        self.assertIsNone(record["escaped_violations"])
        self.assertIn("repairs: 1", (self.root / "metrics-summary.md").read_text())

    def test_failed_repair_cannot_reuse_previous_review_or_test_success(self):
        review = run.ReviewResult(verdict="CHANGES_REQUESTED", summary="Fix the behavior")
        with (
            patch.object(
                run,
                "implementation_attempt",
                side_effect=[({"app": 0}, []), reporting.NeedsInput("Need a fixture")],
            ),
            patch.object(run, "review_changes", return_value=review),
        ):
            with self.assertRaises(reporting.NeedsInput):
                run.execute_build(
                    [{"repair_attempts": 1}], "task", "spec", "", None, False, self.root, {}
                )
        record = self.record()
        self.assertEqual(record["status"], "NEEDS_INPUT")
        self.assertEqual(record["attempts"][0]["review"]["verdict"], "CHANGES_REQUESTED")
        self.assertNotIn("review", record)
        self.assertEqual(record["latest"]["tests"], {})
        self.assertEqual(record["stages"][-1]["status"], "FAILED")
        self.assertIn("not recorded for the current attempt", measurements.render(record))

    def test_publishing_failure_does_not_erase_successful_validation(self):
        config = {
            "project": "app",
            "repository": "org/app",
            "repair_attempts": 0,
            "publish_draft": True,
        }
        states = {
            "app": {"base": "base", "commit": "head", "branch": "branch", "repository": "retained"}
        }
        with (
            patch.object(run, "implementation_attempt", return_value=({"app": 0}, [])),
            patch.object(
                run,
                "review_changes",
                return_value=run.ReviewResult(verdict="PASS", summary="Reviewed"),
            ),
            patch.object(run, "publish", side_effect=TimeoutError("Publication failed")),
        ):
            with self.assertRaises(TimeoutError):
                run.execute_build([config], "task", "spec", "", None, True, self.root, states)
        record = self.record()
        self.assertEqual(record["status"], "PUBLICATION_FAILED")
        self.assertEqual(record["validation"], "PASSED")
        self.assertEqual(record["attempts"][0]["status"], "PASSED")

    def test_interrupted_stage_is_retained_and_context_is_reset(self):
        with self.assertRaises(KeyboardInterrupt):
            with measurements.task(self.root, "task", "feature", {}):
                measurements.begin_attempt(0, "initial")
                with measurements.stage("review"):
                    raise KeyboardInterrupt()
        record = self.record()
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["stages"][0]["error_type"], "KeyboardInterrupt")
        self.assertIsNone(measurements.CURRENT.get())

    def test_metrics_write_failure_preserves_original_error(self):
        with patch.object(measurements, "write", side_effect=OSError("Disk full")):
            with self.assertRaisesRegex(ValueError, "original"):
                with measurements.task(self.root, "task", "feature", {}):
                    raise ValueError("original")
        self.assertIsNone(measurements.CURRENT.get())

    def test_usage_is_a_delta_and_zero_cost_is_unknown(self):
        def metric(tokens):
            return {
                "acp-managed": {
                    "model_name": "model/effort",
                    "accumulated_cost": 0,
                    "accumulated_token_usage": tokens,
                }
            }

        before = metric({"prompt_tokens": 100, "completion_tokens": 20, "cache_read_tokens": 300})
        after = metric({"prompt_tokens": 150, "completion_tokens": 30, "cache_read_tokens": 800})
        conversation = SimpleNamespace(id="conversation", conversation_stats=Mock())
        conversation.conversation_stats.model_dump.return_value = {"usage_to_metrics": after}
        with measurements.task(self.root, "task", "feature", {}):
            measurements.record_agent(
                conversation,
                before,
                time.monotonic(),
                "factory-implementation",
                self.root / "transcript.jsonl",
            )
        usage = self.record()["agents"][0]["usage"][0]
        self.assertEqual(usage["tokens"]["prompt_tokens"], 50)
        self.assertEqual(usage["tokens"]["cache_read_tokens"], 500)
        self.assertIsNone(usage["tokens"]["reasoning_tokens"])
        self.assertIsNone(usage["reported_or_estimated_cost"])

    def test_usage_reset_or_unavailable_baseline_is_not_fabricated(self):
        conversation = SimpleNamespace(id="conversation", conversation_stats=Mock())
        conversation.conversation_stats.model_dump.return_value = {
            "usage_to_metrics": {"acp-managed": {"accumulated_token_usage": {"prompt_tokens": 5}}}
        }
        with measurements.task(self.root, "task", "review", {}):
            measurements.record_agent(
                conversation,
                {"acp-managed": {"accumulated_token_usage": {"prompt_tokens": 50}}},
                time.monotonic(),
                "review",
                None,
            )
            measurements.record_agent(conversation, None, time.monotonic(), "review", None)
        self.assertIsNone(self.record()["agents"][0]["usage"][0]["tokens"]["prompt_tokens"])
        self.assertIsNone(self.record()["agents"][1]["usage"])

    def test_failed_conversation_collects_usage_before_close(self):
        conversation = Mock()
        conversation.id = "conversation"
        conversation.state.events = []
        conversation.conversation_stats.model_dump.side_effect = [
            {"usage_to_metrics": {}},
            {
                "usage_to_metrics": {
                    "acp-managed": {"accumulated_token_usage": {"prompt_tokens": 10}}
                }
            },
        ]
        conversation.run.side_effect = RuntimeError("provider failure")
        with (
            patch.object(agent, "worker_agent"),
            patch.object(agent, "Conversation", return_value=conversation),
            self.assertRaisesRegex(RuntimeError, "provider failure"),
            measurements.task(self.root, "task", "feature", {}),
        ):
            agent.converse(Mock(), "request", transcript=self.root / "transcript.jsonl")
        conversation.close.assert_called_once()
        self.assertEqual(self.record()["agents"][0]["usage"][0]["tokens"]["prompt_tokens"], 10)

    def test_chat_uses_saved_measurement_and_survives_ui_failure(self):
        with measurements.task(self.root, "task", "review", {}):
            measurements.begin_attempt(0, "initial")
            measurements.update({"status": "REVIEWED"})
        report = reporting.TaskReport.__new__(reporting.TaskReport)
        report.config, report.task = {"project": "app"}, "task"
        report.record = {"conversation_id": "conversation"}
        with (
            patch.object(reporting, "write_report") as write,
            patch.object(reporting, "phase"),
            patch.object(reporting, "api"),
            patch.object(reporting, "post", side_effect=RuntimeError("UI offline")) as post,
        ):
            report.update("REVIEWED", "Review completed", metrics=self.root / "metrics.json")
        self.assertEqual(write.call_args.args[2]["metrics"], str(self.root / "metrics.json"))
        self.assertIn("Controller tests: not recorded", post.call_args.args[1])
        self.assertNotIn("Tests passed", post.call_args.args[1])

    def test_operator_observations_do_not_rewrite_automated_evidence(self):
        with measurements.task(self.root, "task", "feature", {}):
            measurements.update({"status": "PASSED"})
        path = self.root / "metrics.json"
        original = path.read_bytes()
        command = [sys.executable, measurements.__file__]
        subprocess.run(
            [
                *command,
                "note",
                str(path),
                "--kind",
                "human_review",
                "--by",
                "maintainer",
                "--minutes",
                "8",
                "--note",
                "Reviewed the contract",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                *command,
                "note",
                str(path),
                "--kind",
                "escaped_violation",
                "--by",
                "maintainer",
                "--reference",
                "issue:123",
                "--note",
                "Boundary case failed after release",
            ],
            check=True,
            capture_output=True,
        )
        self.assertEqual(path.read_bytes(), original)
        notes = [
            json.loads(line) for line in (self.root / "observations.jsonl").read_text().splitlines()
        ]
        self.assertEqual([n["source"] for n in notes], ["operator_reported"] * 2)
        output = subprocess.check_output([*command, "show", str(path)], text=True)
        self.assertIn("issue:123", output)
        invalid = subprocess.run(
            [
                *command,
                "note",
                str(path),
                "--kind",
                "human_review",
                "--by",
                "maintainer",
                "--minutes",
                "nan",
                "--note",
                "invalid",
            ],
            capture_output=True,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(len((self.root / "observations.jsonl").read_text().splitlines()), 2)

    def test_aggregate_separates_unmeasured_effort_from_operator_reports(self):
        directory = self.root / "run"
        directory.mkdir()
        with measurements.task(directory, "task", "maintenance", {}):
            measurements.begin_attempt(0, "pr_maintenance")
            measurements.update({"status": "PASSED"})
        command = [sys.executable, measurements.__file__]

        def report():
            return json.loads(
                subprocess.check_output([*command, "report", str(self.root)], text=True)
            )

        self.assertIsNone(report()["operator_observations"]["reported_minutes"]["human_review"])
        subprocess.run(
            [
                *command,
                "note",
                str(directory / "metrics.json"),
                "--kind",
                "human_review",
                "--by",
                "maintainer",
                "--minutes",
                "7",
                "--note",
                "Reviewed",
            ],
            check=True,
            capture_output=True,
        )
        result = report()
        self.assertEqual(result["operator_observations"]["reported_minutes"]["human_review"], 7)
        self.assertEqual(result["maintenance_runs"], 1)
        self.assertEqual(result["within_run_repairs"], 0)


if __name__ == "__main__":
    unittest.main()
