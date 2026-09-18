"""Protocol failures identify the originating stage without accepting malformed decisions."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import Mock, patch

import agent
import measurements
import run
from pydantic import BaseModel


class Verdict(BaseModel):
    verdict: Literal["PASS", "CHANGES_REQUESTED"]


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conversation = Mock(id="fixture-conversation")
        self.conversation.state.execution_status.value = "finished"
        self.conversation.state.events = []

    def invoke(self, responses, model=run.ImplementationResult, title="Implementation"):
        with (
            patch.object(agent, "worker_agent"),
            patch.object(agent, "Conversation", return_value=self.conversation),
            patch.object(agent, "get_agent_final_response", side_effect=responses),
            patch.object(measurements, "record_agent"),
        ):
            return agent.converse(
                Mock(),
                "Authorized task",
                title=title,
                response_model=model,
                transcript=self.root / "transcript.jsonl",
            )

    def test_empty_question_is_diagnosed_then_repaired_in_same_conversation(self):
        bad = json.dumps({"status": "NEEDS_INPUT", "summary": "Blocked", "questions": []})
        good = json.dumps(
            {
                "status": "NEEDS_INPUT",
                "summary": "Blocked",
                "questions": ["Which accepted policy applies?"],
            }
        )
        with measurements.task(self.root, "task", "feature", {}):
            result = self.invoke([bad, good])
        self.assertEqual(result.status, "NEEDS_INPUT")
        self.assertEqual(self.conversation.run.call_count, 2)
        retry = self.conversation.send_message.call_args.args[0]
        self.assertIn("NEEDS_INPUT requires a concrete question", retry)
        self.assertIn("never invent", retry)
        record = json.loads((self.root / "metrics.json").read_text())
        self.assertEqual(
            [x["kind"] for x in record["response_protocol"]],
            ["invalid_structured_response", "structured_response_repaired"],
        )
        raw = json.loads(next(self.root.glob("*.response-*.json")).read_text())
        self.assertEqual(raw["raw_response"], bad)
        self.assertEqual(raw["expected_schema"], run.ImplementationResult.model_json_schema())

    def test_invalid_json_and_empty_questions_stop_after_two_responses(self):
        for bad in (
            "not JSON SECRET_SENTINEL",
            json.dumps({"status": "NEEDS_INPUT", "summary": "SECRET_SENTINEL", "questions": []}),
        ):
            with self.subTest(bad=bad), self.assertRaises(agent.StructuredResponseError) as raised:
                self.invoke([bad, bad])
            failure = raised.exception
            self.assertEqual(failure.details["stage"], "Implementation")
            self.assertEqual(failure.details["response_attempt"], 2)
            self.assertNotIn("Reviewer", str(failure))
            self.assertNotIn("SECRET_SENTINEL", str(failure))
            self.assertNotIn("input", failure.details["validation_errors"][0])
            self.assertEqual(
                json.loads(Path(failure.details["response_artifact"]).read_text())["raw_response"],
                bad,
            )
        self.conversation.close.assert_called()

    def test_invalid_review_is_not_a_semantic_rejection(self):
        with self.assertRaises(agent.StructuredResponseError) as raised:
            self.invoke(['{"verdict":"MAYBE"}'] * 2, Verdict, "Independent review")
        self.assertEqual(raised.exception.details["kind"], "invalid_structured_response")
        self.assertEqual(raised.exception.details["stage"], "Independent review")

    def test_terminal_build_record_preserves_protocol_classification(self):
        details = {
            "stage": "Implementation",
            "schema_name": "ImplementationResult",
            "response_attempt": 2,
            "validation_errors": [],
            "response_artifact": "retained.json",
            "kind": "invalid_structured_response",
        }
        with (
            patch.object(
                run, "implementation_attempt", side_effect=agent.StructuredResponseError(details)
            ),
            self.assertRaises(agent.StructuredResponseError),
        ):
            run.execute_build(
                [{"repair_attempts": 0}], "task", "spec", "", None, False, self.root, {}
            )
        result = json.loads((self.root / "result.json").read_text())
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["response_failure"], details)
        self.assertEqual(
            json.loads((self.root / "metrics.json").read_text())["latest"]["response_failure"],
            details,
        )
