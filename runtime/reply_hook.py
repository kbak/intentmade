"""Canvas startup extension: notify the factory after a message is persisted.

The production agent-server is compiled. Load through --import-modules rather
than changing a site-packages source file the binary does not execute.
"""

import asyncio
import functools
import inspect
import logging
import os

logger = logging.getLogger(__name__)


async def message_received(conversation_id, message):
    if os.environ.get("FACTORY_REPLY_DISPATCH") != "1" or message.role != "user":
        return
    text = "\n".join(getattr(part, "text", "") for part in message.content)
    if not text.lower().startswith("resume:") or not text[7:].strip():
        return
    process = None
    try:
        # Use the controller's Python environment, not the compiled server's
        # frozen module graph. Pass only the conversation id; the helper reads
        # and validates the persisted answer from the authenticated native API.
        process = await asyncio.create_subprocess_exec(
            "/usr/local/bin/python",
            "/opt/factory/workflows/replies.py",
            conversation_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "OPENHANDS_SUPPRESS_BANNER": "1"},
        )
        await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode:
            raise RuntimeError("Reply dispatch helper failed")
    except Exception as exc:
        if process and process.returncode is None:
            process.kill()
            await process.communicate()
        logger.warning(
            "Immediate task continuation unavailable (%s); scan fallback retained",
            type(exc).__name__,
        )


def install(service_type):
    original = service_type.send_message
    if getattr(original, "factory_reply_hook", False):
        return
    if list(inspect.signature(original).parameters) != [
        "self",
        "message",
        "run",
        "_from_goal_loop",
    ]:
        raise RuntimeError("Pinned Canvas send_message signature changed; review reply integration")

    @functools.wraps(original)
    async def send_message(self, message, run=False, _from_goal_loop=False):
        result = await original(self, message, run, _from_goal_loop=_from_goal_loop)
        await message_received(str(self.stored.id), message)
        return result

    send_message.factory_reply_hook = True
    service_type.send_message = send_message


if os.environ.get("FACTORY_REPLY_DISPATCH") == "1":
    from openhands.agent_server.event_service import EventService

    install(EventService)
