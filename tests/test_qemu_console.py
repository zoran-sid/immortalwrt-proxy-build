"""Serial framing must not confuse command output with protocol markers."""
import unittest

from qemu_smoke import Console


class ConsoleProtocolTests(unittest.TestCase):
    def test_b_and_e_prefixed_payloads_are_not_boundaries(self):
        con = object.__new__(Console)
        con.clear = lambda: None
        con.send = lambda *args, **kwargs: None
        reads = iter([b"echo command\r\n@@B\r\n@@ENABLED\r\n",
                      b"@@BAKED\r\n@@E\r\nroot@router:~# "])
        con.read = lambda timeout: next(reads)
        result, _ = con.command("fixture")
        self.assertEqual(result, "ENABLED\nBAKED")


if __name__ == "__main__":
    unittest.main()
