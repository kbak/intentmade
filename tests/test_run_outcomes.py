"""Exercise the installed callback with native and factory result metadata."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from openhands.automation import router
from openhands.automation.schemas import RunCompleteRequest


class NativeOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_preserves_factory_outcomes_and_native_finish_summary(self):
        for status in ("COMPLETED", "SKIPPED"):
            with self.subTest(status=status):
                record = SimpleNamespace(
                    id=uuid4(),
                    automation=SimpleNamespace(id=uuid4(), user_id="operator", org_id=None),
                    run_metadata={"existing": "retained"},
                    sandbox_id=None,
                    status_detail=None,
                )
                selected = Mock()
                selected.scalars.return_value.first.return_value = record
                session = SimpleNamespace(
                    execute=AsyncMock(side_effect=[selected, SimpleNamespace(rowcount=1)]),
                    refresh=AsyncMock(),
                )
                outcome = {"status": status, "summary": "No eligible work"}
                finish = {"message": "Native summary"}
                body = RunCompleteRequest(
                    status=status,
                    conversation_id="report-conversation",
                    task_outcome=outcome,
                    blocking_factor={"kind": "waiting"},
                )
                with (
                    patch.object(
                        router,
                        "fetch_latest_finish_tool_response_for_run",
                        new=AsyncMock(return_value=finish),
                    ),
                    patch.object(router, "capture_automation_event", new=AsyncMock()),
                    patch.object(router, "record_first_run_outcome", new=AsyncMock()),
                    patch.object(
                        router.AutomationRunResponse, "model_validate", return_value=record
                    ),
                ):
                    await router.complete_run(
                        record.id,
                        body,
                        Mock(),
                        user=SimpleNamespace(user_id="operator", org_id=None),
                        session=session,
                    )
                values = session.execute.call_args_list[1].args[0].compile().params
                self.assertEqual(values["status"].value, status)
                self.assertEqual(values["conversation_id"], "report-conversation")
                self.assertEqual(
                    values["run_metadata"],
                    {
                        "existing": "retained",
                        "task_outcome": outcome,
                        "blocking_factor": {"kind": "waiting"},
                        "finish_tool_response": finish,
                    },
                )


if __name__ == "__main__":
    unittest.main()
