"""Serial framing must not confuse command output with protocol markers."""
import unittest
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from qemu_smoke import Console


class ConsoleProtocolTests(unittest.TestCase):
    def test_b_and_e_prefixed_payloads_are_not_boundaries(self):
        con = object.__new__(Console)
        con.clear = lambda: None
        con.send = lambda *args, **kwargs: None
        reads = iter([b"echo command\r\n@@B:00000001\r\n@@ENABLED\r\n",
                      b"@@BAKED\r\n@@E:00000001\r\nroot@router:~# "])
        con.read = lambda timeout: next(reads)
        result, _ = con.command("fixture")
        self.assertEqual(result, "ENABLED\nBAKED")

    def test_previous_response_cannot_complete_the_next_command(self):
        con = object.__new__(Console)
        con.clear = lambda: None
        con.send = lambda *args, **kwargs: None
        reads = iter([b"@@B:00000001\n@@FIRST\n@@E:00000001\n",
                      b"@@B:00000001\n@@STALE\n@@E:00000001\n",
                      b"@@B:00000002\n@@SECOND\n@@E:00000002\n"])
        con.read = lambda timeout: next(reads)
        self.assertEqual(con.command("one")[0], "FIRST")
        self.assertEqual(con.command("two")[0], "SECOND")

    @unittest.skipUnless(os.environ.get("TEST_SH") or shutil.which("sh"), "Shell required")
    def test_unterminated_output_cannot_swallow_the_end_marker(self):
        con = object.__new__(Console)
        con.clear = lambda: None
        responses = []
        with tempfile.TemporaryDirectory() as tmp:
            output = shlex.quote((Path(tmp) / "output").as_posix())
            def send(command, **kwargs):
                command = command.replace("/tmp/.smk.out", output).rstrip("\r")
                result = subprocess.run([os.environ.get("TEST_SH") or shutil.which("sh"), "-c", command],
                                        capture_output=True, check=True)
                responses.append(result.stdout)
            con.send = send
            con.read = lambda timeout: responses.pop(0)
            result, _ = con.command("printf UNIQUE_TAGS")
        self.assertEqual(result, "UNIQUE_TAGS")


if __name__ == "__main__":
    unittest.main()
