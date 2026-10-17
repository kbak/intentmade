"""Infrastructure recovery preserves code and does not invoke another implementer."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run
import traceability
from pipeline_fixture import TraceabilityScenario


class ValidationRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_real_pipeline_keeps_candidate_and_restores_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            scenario = TraceabilityScenario(self, Path(directory))
            scenario.config.update(repair_attempts=1, validation_baseline_manifest=True)
            counter = Path(directory) / "check-count"
            scenario.config["traceability_scope"]["tests"] = {
                "format": "command",
                "command": [
                    "python",
                    "-c",
                    f"import os,hashlib; from pathlib import Path; b=Path(os.environ['FACTORY_VALIDATION_BASE_MANIFEST']).read_bytes(); assert hashlib.sha256(b).hexdigest()==os.environ['FACTORY_VALIDATION_BASE_SHA256']; p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); raise SystemExit(75 if n==1 else 0)",
                ],
                "timeout_seconds": 30,
            }

            def edit(root, attempt):
                path = root / "session.py"
                path.write_text(path.read_text() + "\n# approved edit\n")
                manifest = next(scenario.root.glob("job-*/traceability/pilot/validation-base.json"))
                manifest.write_text("worker-controlled replacement")

            result = scenario.invoke(edit)
            # [utest->req~im-bounded-repair~1]
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(scenario.attempts), 1)
            self.assertEqual(counter.read_text(), "2")
            metrics = json.loads((scenario.artifact / "metrics.json").read_text())
            commits = [a["outcome"]["repositories"]["pilot"]["commit"] for a in metrics["attempts"]]
            self.assertEqual(commits[0], commits[1])
            baseline = json.loads((scenario.artifact / "pilot/validation-base-0.json").read_text())
            self.assertEqual(baseline["base"], scenario.states["pilot"]["base"])
            self.assertEqual(len(scenario.review_prompts), 1)

    def test_typed_infrastructure_failures_only(self):
        for tests, expected in [
            ({"exit_code": 75}, ["pilot"]),
            ({"status": "error", "error": "Command timed out after 900 seconds"}, ["pilot"]),
            ({"exit_code": 1, "status": "failed"}, []),
            ({"exit_code": 0, "status": "passed"}, []),
        ]:
            with self.subTest(tests=tests):
                states = {
                    "pilot": {"traceability": {"tests": {"source_status": "matched", **tests}}}
                }
                self.assertEqual(
                    traceability.infrastructure_failures({"pilot": 1}, states), expected
                )
                states["pilot"]["traceability"]["tests"]["source_status"] = "changed"
                self.assertEqual(traceability.infrastructure_failures({"pilot": 1}, states), [])
                self.assertEqual(
                    traceability.infrastructure_failures({"pilot": 1, "other": 1}, states), []
                )

    def test_retries_validation_only_and_still_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configs = [
                {"project": "pilot", "repair_attempts": 1, "repository": "", "publish_draft": True}
            ]
            states = {
                "pilot": {
                    "base": "base",
                    "commit": "head",
                    "branch": "task",
                    "repository": "retained",
                }
            }
            calls = []

            def attempt(*args, **kwargs):
                calls.append(kwargs)
                states["pilot"]["traceability"] = {
                    "tests": {"exit_code": 75, "source_status": "matched"}
                }
                return ({"pilot": 1 if len(calls) == 1 else 0}, ["infrastructure"])

            with (
                patch.object(run, "implementation_attempt", side_effect=attempt),
                patch.object(run.reporting, "progress_stage") as progress,
                patch.object(run.repair_context, "retain"),
                patch.object(run.traceability, "record_review"),
                patch.object(
                    run,
                    "review_changes",
                    return_value=run.ReviewResult(verdict="PASS", summary="Reviewed"),
                ) as review,
                patch.object(run, "publish", return_value="https://example.invalid/pr"),
            ):
                result = run.execute_build(
                    configs, "task", "approved scope", "", None, True, root, states
                )
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(calls, [{}, {"validation_only": True}])
            self.assertIn(("implementation", "Reused"), [c.args for c in progress.call_args_list])
            review.assert_called_once()
            metrics = json.loads((root / "metrics.json").read_text())
            self.assertEqual(metrics["attempts"][1]["reason"], "validation_infrastructure")
