"""Protocol failures identify the originating stage without accepting malformed decisions."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import Mock, call, patch

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

    def test_provider_http_failure_stops_without_format_retry(self):
        for status, code in (
            (401, "ACPAuthRequired"),
            (403, "ACPProviderError"),
            (503, "ACPProviderError"),
        ):
            self.conversation.reset_mock()
            raw = f"unexpected status {status}: SECRET_SENTINEL, auth error code: token_revoked"
            with (
                self.subTest(status=status),
                measurements.task(self.root, "task", "feature", {}),
                self.assertRaises(agent.AgentExecutionError) as caught,
            ):
                self.invoke([raw])
            # [utest~im-protocol-provider-failure~1->req~im-agent-failure-reporting~1]
            self.conversation.run.assert_called_once()
            self.conversation.send_message.assert_called_once()
            self.conversation.close.assert_called_once()
            details = caught.exception.details
            self.assertEqual(details["code"], code)
            self.assertEqual(details["http_status"], status)
            self.assertNotIn("SECRET_SENTINEL", str(caught.exception))
            self.assertNotIn("JSON", str(caught.exception))
            retained = json.loads(Path(details["response_artifact"]).read_text())
            self.assertEqual(retained["raw_response"], raw)
            self.assertEqual(
                json.loads((self.root / "metrics.json").read_text())["response_protocol"][0][
                    "kind"
                ],
                "agent_execution_failure",
            )
            if status == 401:
                self.assertIn("factoryctl codex-login", str(caught.exception))

    def test_provider_diagnostics_inside_decisions_are_not_failures(self):
        diagnostic = "unexpected status 401 Unauthorized: token_revoked"
        valid = json.dumps({"status": "IMPLEMENTED", "summary": diagnostic})
        # [utest~im-protocol-provider-text~1->req~im-agent-failure-reporting~1]
        self.assertEqual(self.invoke([valid]).summary, diagnostic)
        self.conversation.reset_mock()
        self.assertEqual(
            self.invoke(["Investigate " + diagnostic], model=None), "Investigate " + diagnostic
        )
        self.conversation.run.assert_called_once()

    def test_capacity_after_progress_continues_same_conversation_and_model(self):
        # [utest~im-capacity-recovery~1->req~im-agent-failure-reporting~1]
        for prefix in ("", "Completed work.\n", "Completed work."):
            with self.subTest(prefix=prefix), patch.object(agent.time, "sleep") as sleep:
                self.conversation.reset_mock()
                raw = prefix + agent.CAPACITY_MESSAGE
                with measurements.task(self.root, "task", "feature", {}):
                    result = self.invoke(
                        [raw, '{"status":"IMPLEMENTED","summary":"Finished retained work"}']
                    )
                self.assertEqual(result.status, "IMPLEMENTED")
                self.assertEqual(self.conversation.run.call_count, 2)
                self.conversation.close.assert_called_once()
                sleep.assert_called_once_with(10)
                continuation = self.conversation.send_message.call_args.args[0]
                self.assertIn("existing workspace", continuation)
                self.assertIn("Inspect the last operation's result", continuation)
                self.assertNotIn("Restate your final response", continuation)
                self.assertNotIn("Do not use tools", continuation)
                events = json.loads((self.root / "metrics.json").read_text())["response_protocol"]
                self.assertEqual(
                    [e["kind"] for e in events], ["agent_execution_failure", "agent_capacity_retry"]
                )
                retained = json.loads(Path(events[0]["response_artifact"]).read_text())
                self.assertEqual(retained["raw_response"], raw)

    def test_repeated_capacity_is_bounded_and_retains_provider_error(self):
        # [utest~im-capacity-exhaustion~1->req~im-agent-failure-reporting~1]
        with (
            patch.object(agent.time, "sleep") as sleep,
            self.assertRaises(agent.AgentExecutionError) as caught,
        ):
            self.invoke([agent.CAPACITY_MESSAGE] * 3)
        self.assertEqual(caught.exception.details["code"], "ACPModelCapacity")
        self.assertEqual(caught.exception.details["response_attempt"], 3)
        self.assertIsNone(caught.exception.details["http_status"])
        self.assertEqual(self.conversation.run.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(10), call(30)])
        self.assertEqual(len(list(self.root.glob("*.failure-*.json"))), 3)
        self.assertFalse(list(self.root.glob("*.response-*.json")))

    def test_capacity_does_not_consume_or_reset_format_repair(self):
        good = '{"status":"IMPLEMENTED","summary":"done"}'
        for responses in (
            [agent.CAPACITY_MESSAGE, "malformed", good],
            ["malformed", agent.CAPACITY_MESSAGE, good],
        ):
            with self.subTest(responses=responses), patch.object(agent.time, "sleep"):
                self.conversation.reset_mock()
                self.assertEqual(self.invoke(responses).status, "IMPLEMENTED")
                self.assertEqual(self.conversation.run.call_count, 3)
        self.conversation.reset_mock()
        with patch.object(agent.time, "sleep"), self.assertRaises(agent.StructuredResponseError):
            self.invoke(["malformed", agent.CAPACITY_MESSAGE, "still malformed"])
        self.assertEqual(self.conversation.run.call_count, 3)

    def test_capacity_text_in_decisions_and_prose_is_not_a_provider_error(self):
        diagnostic = agent.CAPACITY_MESSAGE
        for value in (
            json.dumps({"status": "IMPLEMENTED", "summary": diagnostic}),
            "Investigate " + diagnostic,
            'The provider said "' + diagnostic + '"',
            diagnostic + " We retained the work.",
        ):
            with self.subTest(value=value), patch.object(agent.time, "sleep") as sleep:
                self.conversation.reset_mock()
                self.assertEqual(self.invoke([value], model=None), value)
                self.conversation.run.assert_called_once()
                sleep.assert_not_called()

    def test_capacity_retry_does_not_retry_other_provider_errors(self):
        with (
            patch.object(agent.time, "sleep") as sleep,
            self.assertRaises(agent.AgentExecutionError) as caught,
        ):
            self.invoke([agent.CAPACITY_MESSAGE, "unexpected status 401 Unauthorized"])
        self.assertEqual(caught.exception.details["code"], "ACPAuthRequired")
        sleep.assert_called_once_with(10)
        self.assertEqual(self.conversation.run.call_count, 2)

    def test_provider_failure_survives_unavailable_artifact_storage(self):
        with (
            patch.object(Path, "write_text", side_effect=OSError("artifact storage unavailable")),
            self.assertRaises(agent.AgentExecutionError) as caught,
        ):
            self.invoke(["unexpected status 401 Unauthorized"])
        self.assertEqual(caught.exception.details["code"], "ACPAuthRequired")
        self.assertIsNone(caught.exception.details["response_artifact"])
        self.assertEqual(caught.exception.details["retention_error"], "OSError")
        self.conversation.run.assert_called_once()

    def test_terminal_build_record_preserves_provider_failure(self):
        details = {
            "kind": "agent_execution_failure",
            "stage": "Implementation",
            "code": "ACPAuthRequired",
            "detail": "Reconnect Codex, then explicitly resume.",
            "http_status": 401,
            "response_artifact": "retained.json",
        }
        with (
            patch.object(
                run, "implementation_attempt", side_effect=agent.AgentExecutionError(details)
            ),
            patch.object(run, "review_changes") as review,
            patch.object(run, "publish") as publish,
            self.assertRaises(agent.AgentExecutionError),
        ):
            run.execute_build(
                [{"repair_attempts": 1}], "task", "spec", "", None, True, self.root, {}
            )
        # [utest~im-protocol-provider-outcome~1->req~im-agent-failure-reporting~1]
        result = json.loads((self.root / "result.json").read_text())
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["response_failure"], details)
        review.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(
            json.loads((self.root / "metrics.json").read_text())["latest"]["response_failure"],
            details,
        )

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
