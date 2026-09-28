"""Recover startup timeouts without replaying work or hiding revoked credentials."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent
import diagnostics


def event(identifier, kind="ConversationErrorEvent", **fields):
    data = {"id": identifier, "kind": kind, "source": "agent", **fields}
    return SimpleNamespace(id=identifier, model_dump=lambda **kwargs: data)


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conversation = Mock(id="conversation")
        self.conversation.state.events = []

    def fail(self, code, detail="startup failed"):
        self.conversation.state.events.append(
            event(str(len(self.conversation.state.events)), code=code, detail=detail)
        )
        raise RuntimeError("Remote conversation ended with error")

    def test_temporary_startup_timeout_retries_once_and_retains_reason(self):
        def run():
            if self.conversation.run.call_count == 1:
                self.fail("ACPStartupTimeout", "Authentication endpoint timed out")

        self.conversation.run.side_effect = run
        with tempfile.TemporaryDirectory() as directory, patch.object(agent.time, "sleep"):
            transcript = Path(directory) / "review.jsonl"
            agent.run_with_startup_recovery(self.conversation, transcript)
            record = json.loads(next(Path(directory).glob("*-startup-*.jsonl")).read_text())
        self.assertEqual(self.conversation.run.call_count, 2)
        self.assertTrue(record["retrying"])
        self.assertEqual(record["code"], "ACPStartupTimeout")

    def test_repeated_timeout_is_bounded(self):
        self.conversation.run.side_effect = lambda: self.fail("ACPStartupTimeout")
        with (
            patch.object(agent.time, "sleep"),
            self.assertRaises(agent.AgentStartupError) as caught,
        ):
            agent.run_with_startup_recovery(self.conversation)
        self.assertEqual(self.conversation.run.call_count, 2)
        self.assertEqual(caught.exception.details["attempt"], 2)
        self.assertFalse(caught.exception.details["retrying"])

    def test_generic_server_wrapper_does_not_hide_typed_startup_failure(self):
        def run():
            self.conversation.state.events.extend(
                [
                    event("typed", code="ACPAuthRequired", detail="Reconnect Codex"),
                    event("wrapper", code="RequestError", source="environment"),
                ]
            )
            raise RuntimeError("RequestError")

        self.conversation.run.side_effect = run
        with self.assertRaisesRegex(agent.AgentStartupError, "ACPAuthRequired"):
            agent.run_with_startup_recovery(self.conversation)
        self.conversation.run.assert_called_once()

    def test_auth_spawn_and_unknown_initialization_failures_are_not_retried(self):
        for code in ("ACPAuthRequired", "ACPSpawnError", "ACPInitError"):
            self.conversation.run.reset_mock()
            self.conversation.run.side_effect = lambda: self.fail(code)
            with self.subTest(code=code), self.assertRaisesRegex(agent.AgentStartupError, code):
                agent.run_with_startup_recovery(self.conversation)
            self.conversation.run.assert_called_once()

    def test_untyped_or_prompt_failure_never_replays_work(self):
        for code in (None, "ACPPromptError"):
            self.conversation.run.reset_mock()
            self.conversation.run.side_effect = (
                RuntimeError("connection dropped") if code is None else lambda: self.fail(code)
            )
            with self.subTest(code=code), self.assertRaises(RuntimeError) as caught:
                agent.run_with_startup_recovery(self.conversation)
            self.assertNotIsInstance(caught.exception, agent.AgentStartupError)
            self.conversation.run.assert_called_once()

    def test_timeout_after_agent_activity_does_not_replay(self):
        def run():
            self.conversation.state.events.append(event("action", kind="ACPToolCallEvent"))
            self.fail("ACPStartupTimeout")

        self.conversation.run.side_effect = run
        with self.assertRaises(agent.AgentStartupError):
            agent.run_with_startup_recovery(self.conversation)
        self.conversation.run.assert_called_once()


class WorkerDiagnosticsTests(unittest.TestCase):
    def test_native_session_capture_is_bounded_redacted_and_preserves_writer_offset(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as log:
            log.write(b"x" * 2_000_010 + b"worker-api-key")
            log.flush()
            offset = log.tell()
            path = diagnostics.retain_worker(
                SimpleNamespace(_factory_log=log), directory, ["worker-api-key"]
            )
            self.assertLessEqual(path.stat().st_size, 2_000_000)
            self.assertNotIn("worker-api-key", path.read_text())
            self.assertTrue(path.read_text().endswith("[REDACTED]"))
            self.assertEqual(log.tell(), offset)
            log.write(b" next event")
            self.assertEqual(log.tell(), offset + len(b" next event"))

    def test_old_refreshed_and_unknown_tokens_are_redacted(self):
        credentials = [
            json.dumps({"tokens": {"access_token": "old-access", "refresh_token": "old-refresh"}}),
            json.dumps({"tokens": {"access_token": "new-access", "refresh_token": "new-refresh"}}),
            "worker-api-key",
        ]
        message = "old-access old-refresh new-access new-refresh worker-api-key refresh_token=unknown-refresh"
        result = diagnostics.redact(message, credentials)
        for value in (
            "old-access",
            "old-refresh",
            "new-access",
            "new-refresh",
            "worker-api-key",
            "unknown-refresh",
        ):
            self.assertNotIn(value, result)
        self.assertIn("[REDACTED]", result)

    def test_capture_is_bounded_unique_and_uses_actual_worker(self):
        workspace = SimpleNamespace(_container_id="a" * 64)
        result = SimpleNamespace(returncode=0, stdout="x" * 2_000_010, stderr="startup failed")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(diagnostics.subprocess, "run", return_value=result) as command,
        ):
            path = diagnostics.retain_worker(workspace, directory)
            self.assertLessEqual(path.stat().st_size, 2_000_000)
            self.assertTrue(path.read_text().endswith("startup failed"))
            self.assertEqual(command.call_args.args[0][-1], "a" * 64)
            with self.assertRaises(FileExistsError):
                diagnostics.retain_worker(workspace, directory)


if __name__ == "__main__":
    unittest.main()
