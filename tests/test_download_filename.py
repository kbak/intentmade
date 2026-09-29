"""Execute the installed native download route against a disposable upload."""

import ast
import asyncio
import importlib.util
import re
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import unquote

from starlette.responses import Response

RUNTIME = Path(__file__).resolve().parent.parent / "runtime"
if not RUNTIME.is_dir():
    RUNTIME = Path("/opt/factory")


class DownloadFilenameTests(unittest.TestCase):
    def test_native_route_preserves_bytes_and_encodes_unicode_names(self):
        spec = importlib.util.spec_from_file_location(
            "download_patch", RUNTIME / "patch_download_filename.py"
        )
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        source = Path(importlib.util.find_spec("openhands.automation.router").origin).read_text()
        if "def _factory_download_disposition(" not in source:
            source = patch.patch_router(source)
        with self.assertRaisesRegex(RuntimeError, "route changed"):
            patch.patch_router(source)
        functions = [
            n
            for n in ast.parse(source).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name in ("_factory_download_disposition", "download_automation_tarball")
        ]
        self.assertEqual(len(functions), 2)
        for function in functions:
            function.decorator_list = []
            function.returns = None
            function.args.defaults = []
            for arg in function.args.args:
                arg.annotation = None
        namespace = {
            "re": re,
            "asyncio": asyncio,
            "Response": Response,
            "_get_org_automation": AsyncMock(),
            "parse_internal_upload_id": lambda path: "upload",
            "select": Mock(),
            "TarballUpload": Mock(),
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
                "native-download-route",
                "exec",
            ),
            namespace,
        )
        session = SimpleNamespace(execute=AsyncMock(return_value=Mock()))
        session.execute.return_value.scalars.return_value.first.return_value = SimpleNamespace(
            storage_path="retained-upload"
        )
        storage = Mock()
        storage.read.return_value = b"unchanged tarball bytes\x00\xff"
        names = [
            "Factory",
            "Factory — example-project",
            "计划 🧪 café",
            '"/\\\r\n',
            'hello\r\nHeader: bad/"',
        ]
        for name in names:
            with self.subTest(name=name):
                namespace["_get_org_automation"].return_value = SimpleNamespace(
                    name=name, tarball_path="oh-internal://uploads/id"
                )
                response = asyncio.run(
                    namespace["download_automation_tarball"](
                        uuid.uuid4(), SimpleNamespace(org_id="org"), session, storage
                    )
                )
                self.assertEqual(response.body, storage.read.return_value)
                header = response.headers["content-disposition"]
                header.encode("ascii")
                self.assertNotIn("\r", header)
                self.assertNotIn("\n", header)
                safe = re.sub(r'[\x00-\x1f\x7f"\\/]', "", name) or "automation"
                self.assertEqual(unquote(header.split("filename*=UTF-8''", 1)[1]), safe + ".tar")
                self.assertNotIn("/", header)
        storage.read.assert_called_with("retained-upload")


if __name__ == "__main__":
    unittest.main()
