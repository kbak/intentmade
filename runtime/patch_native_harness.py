"""Prevent ambient repository plugins from adding executors to factory agents."""

import ast
from importlib.metadata import version
from pathlib import Path

OLD = """            ambient_plugins = load_available_plugins(
                work_dir=self.workspace.working_dir,
                include_user=True,
                include_project=True,
            )"""
NEW = """            # Factory workers and controller report readers use explicit tools.
            # Ambient plugins can add MCP executors and shell hooks before a turn.
            import os
            factory_managed = os.environ.get("FACTORY_HEADLESS") == "1" or any(
                tool.name in {"factory_reader", "FactoryReaderTool"} for tool in self.state.agent.tools
            )
            ambient_plugins = {} if factory_managed else load_available_plugins(
                work_dir=self.workspace.working_dir,
                include_user=True,
                include_project=True,
            )"""


# [impl->req~im-native-read-only~1]
def patch(source):
    if source.count(OLD) != 1:
        raise RuntimeError("Pinned ambient plugin loader changed; review native factory policy")
    result = source.replace(OLD, NEW, 1)
    ast.parse(result)
    return result


if __name__ == "__main__":
    if version("openhands-sdk") != "1.54.0":
        raise RuntimeError("Native factory policy requires OpenHands SDK 1.54.0")
    from openhands.sdk.conversation.impl import local_conversation

    path = Path(local_conversation.__file__)
    path.write_text(patch(path.read_text()))
