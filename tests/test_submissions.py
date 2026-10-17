"""Frozen manual submissions resume through the same controller reply queue."""

import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import common
import reporting
import run
import sdlc
import submissions

CONFIG = {"project": "example", "repository": "org/repo", "branch": "main"}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for target in (common, reporting):
            self.stack.enter_context(patch.object(target, "DATA", self.root))
        self.stack.enter_context(patch.object(reporting, "ACTIVE", None))
        self.stack.enter_context(patch.object(reporting, "post"))
        self.stack.enter_context(
            patch.object(reporting, "api", return_value={"execution_status": "finished"})
        )
        self.task = "issue-17"
        self.bases = {"example": "a" * 40}
        self.package = sdlc.propose(
            self.task,
            self.bases,
            {
                "intent.md": "Preserve proofs.\n",
                "spec.md": "# Accepted\nPreserve data and\nrequirement IDs.\nDo not change semantics.\n",
                "plan.md": "Keep completed code and validate.\n",
            },
        )
        self.request = self.package["documents"]["spec.md"]
        reporting.write_report(
            CONFIG,
            self.task,
            {
                "task": self.task,
                "repository": CONFIG["repository"],
                "snapshot": None,
                "conversation_id": "original-thread",
                "status": "NEEDS_INPUT",
                "updated_at": "2026-10-17T04:11:00+00:00",
                "result": {
                    "error": "Frozen references parse as duplicate declarations",
                    "repositories": {},
                },
            },
        )
        self.submission = submissions.remember(
            [CONFIG], self.task, self.request, self.bases, package=self.package
        )
        self.reply = self.stack.enter_context(
            patch.object(
                reporting,
                "resume_reply",
                return_value={
                    "id": "explicit-user-event",
                    "answer": "Authorize the formatting-only correction.",
                    "snapshot": None,
                },
            )
        )
        self.failure = None
        self.build = self.stack.enter_context(
            patch.object(
                run,
                "build_group",
                side_effect=self.execute,
            )
        )

    def execute(self, *args, **kwargs):
        kwargs["continuation"]["on_start"]()
        if self.failure:
            raise self.failure
        return {"status": "PASSED", "repositories": {"example": {"commit": "b" * 40}}}

    def test_frozen_submission_resumes_once_with_original_inputs_and_thread(self):
        original = copy.deepcopy(self.submission)
        # [utest~im-submitted-resume~1->req~im-explicit-resume~1]
        self.assertEqual(submissions.process(CONFIG, "credential", 1), (1, 0))
        args, kwargs = self.build.call_args
        self.assertEqual(args[:4], ([CONFIG], self.task, self.request, self.bases))
        self.assertEqual(kwargs["work_package"], self.package)
        self.assertIn("formatting-only", kwargs["continuation"]["answers"][0])
        self.assertIn("duplicate declarations", kwargs["continuation"]["failure"])
        record = reporting.read_report(CONFIG, self.task)
        self.assertEqual(record["conversation_id"], "original-thread")
        self.assertEqual(record["answer_id"], "explicit-user-event")
        self.assertEqual(record["submission"], original)
        self.assertEqual(submissions.process(CONFIG, "credential", 1), (0, 0))
        self.build.assert_called_once()

    def test_missing_answer_tampered_submission_and_retargeted_repository_do_not_build(self):
        self.reply.return_value = None
        self.assertEqual(submissions.process(CONFIG, "credential", 1), (0, 0))
        self.reply.return_value = {"id": "new", "answer": "retry", "snapshot": None}
        with self.assertRaisesRegex(ValueError, "repository or base branch changed"):
            submissions.process({**CONFIG, "branch": "other"}, "credential", 1)
        path = reporting.report_path(CONFIG, self.task)
        record = json.loads(path.read_text())
        record["submission"]["payload"]["request"] = "Do unrelated work"
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "submission changed"):
            submissions.process(CONFIG, "credential", 1)
        self.build.assert_not_called()

    def test_failed_attempt_consumes_answer_and_needs_a_fresh_reply(self):
        self.failure = RuntimeError("verification infrastructure unavailable")
        with self.assertRaisesRegex(RuntimeError, "verification infrastructure"):
            submissions.process(CONFIG, "credential", 1)
        self.assertEqual(reporting.read_report(CONFIG, self.task)["status"], "FAILED")
        self.assertEqual(submissions.process(CONFIG, "credential", 1), (0, 0))
        self.build.assert_called_once()
        self.reply.return_value = {
            "id": "second-explicit-event",
            "answer": "retry",
            "snapshot": None,
        }
        self.failure = None
        self.assertEqual(submissions.process(CONFIG, "credential", 1), (1, 0))
        self.assertEqual(self.build.call_count, 2)

    def test_busy_repository_does_not_consume_the_queued_answer(self):
        before = reporting.read_report(CONFIG, self.task)
        self.build.side_effect = BlockingIOError("another build owns the repository")
        self.assertEqual(submissions.process(CONFIG, "credential", 1), (0, 0))
        self.assertEqual(reporting.read_report(CONFIG, self.task), before)

    def test_formatting_authorization_is_event_package_and_line_bound(self):
        grant = submissions.authorize_formatting(
            CONFIG, self.task, "explicit-user-event", {"spec.md": [2, 3]}
        )
        state = {"formatting_amendment": grant}
        # [utest~im-package-formatting-amendment~1->req~im-portable-plan~2]
        self.assertTrue(
            sdlc.permitted_formatting(
                state,
                self.package,
                "spec.md",
                b"# Accepted\nPreserve data and requirement IDs.\nDo not change semantics.\n",
            )
        )
        self.assertFalse(
            sdlc.permitted_formatting(
                state,
                self.package,
                "spec.md",
                b"# Accepted\nDelete data and requirement IDs.\nDo not change semantics.\n",
            )
        )
        self.assertFalse(
            sdlc.permitted_formatting(
                state,
                self.package,
                "spec.md",
                b"#   Accepted\nPreserve data and requirement IDs.\nDo not change semantics.\n",
            )
        )
        self.assertFalse(
            sdlc.permitted_formatting({}, self.package, "spec.md", self.request.encode())
        )
        self.assertFalse(
            sdlc.permitted_formatting(state, self.package, "accepted.json", b"changed")
        )
        with self.assertRaisesRegex(ValueError, "fresh explicit answer"):
            submissions.authorize_formatting(
                CONFIG, self.task, "invented-event", {"spec.md": [2, 3]}
            )
        for ranges in ({"accepted.json": [1, 1]}, {"spec.md": [0, 3]}, {"spec.md": [3, 99]}):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                submissions.authorize_formatting(CONFIG, self.task, "explicit-user-event", ranges)
        changed = copy.deepcopy(self.package)
        changed["documents"]["spec.md"] += "Extra obligation."
        changed = sdlc.propose(self.task, self.bases, changed["documents"])
        with self.assertRaisesRegex(ValueError, "another accepted package"):
            sdlc.permitted_formatting(state, changed, "spec.md", self.request.encode())

    def test_published_and_issue_snapshot_tasks_keep_their_existing_resume_routes(self):
        record = reporting.read_report(CONFIG, self.task)
        for details in (
            {"snapshot": {"issue": 17}},
            {
                "result": {
                    "repositories": {
                        "example": {"pull_request": "https://github.com/org/repo/pull/1"}
                    }
                }
            },
        ):
            reporting.write_report(CONFIG, self.task, {**record, **details})
            self.assertEqual(submissions.process(CONFIG, "credential", 1), (0, 0))
        self.build.assert_not_called()
