import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("build_inputs", "/runtime/build_inputs.py")
build_inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_inputs)


class RuntimeBuildTests(unittest.TestCase):
    def test_patch_additions_removals_modes_and_lock_changes_require_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (*build_inputs.FILES, "runtime/patch.py"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original")
            recorded = build_inputs.manifest(root)
            # [utest~im-build_inputs-RuntimeBuildTests-patch_additions_removals_modes_and_lock_changes_require_rebuild~1->req~im-runtime-build-inputs~1]
            self.assertEqual(build_inputs.verify(root, recorded), recorded)
            for name in (*build_inputs.FILES, "runtime/patch.py", "runtime/new_patch.py"):
                path = root / name
                existed = path.exists()
                path.write_text("changed")
                with self.assertRaisesRegex(ValueError, "rebuild and pin"):
                    build_inputs.verify(root, recorded)
                if existed:
                    path.write_text("original")
                else:
                    path.unlink()
            patch = root / "runtime/patch.py"
            patch.chmod(0o755)
            with self.assertRaises(ValueError):
                build_inputs.verify(root, recorded)
            patch.unlink()
            with self.assertRaises(ValueError):
                build_inputs.verify(root, recorded)
            patch.write_text("original")
            (root / "runtime/alias").symlink_to("../docker", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "regular file"):
                build_inputs.manifest(root)
