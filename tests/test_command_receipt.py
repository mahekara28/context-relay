import sys
import tempfile
import unittest

from context_relay.command_receipt import run_command


class CommandReceiptTests(unittest.TestCase):
    def test_receipt_hashes_output_without_storing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = run_command([sys.executable, "-c", "print('private-output')"], cwd=temporary)
        self.assertEqual(result["status"], "exited")
        self.assertEqual(result["exit_code"], 0)
        self.assertGreater(result["stdout"]["bytes"], 0)
        self.assertNotIn("private-output", str(result))

    def test_timeout_receipt_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = run_command([sys.executable, "-c", "import time; time.sleep(3)"],
                                 cwd=temporary, timeout_seconds=1)
        self.assertEqual(result["status"], "timed_out")


if __name__ == "__main__":
    unittest.main()
