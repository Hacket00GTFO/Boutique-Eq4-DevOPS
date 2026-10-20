"""Only fixed diagnostics, never raw AWS output, may enter the tunnel log."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssm_tunnel import session_diagnostic


class SessionDiagnosticsTests(unittest.TestCase):
    def test_aws_failure_diagnostics_do_not_forward_request_details(self):
        sentinel = 'isolated-sensitive-fixture'
        cases = [
            ('ExpiredTokenException', 'expired'),
            ('Unable to locate credentials', 'missing'),
            ('InvalidClientTokenId', 'invalid'),
            ('AccessDeniedException', 'denied'),
            ('TargetNotConnected', 'not connected'),
            ('SessionManagerPlugin is not found', 'plugin is missing'),
        ]
        for code, expected in cases:
            with self.subTest(code=code):
                message = session_diagnostic(code + ': ' + sentinel)
                self.assertIn(expected, message)
                self.assertNotIn(sentinel, message)

    def test_other_process_output_is_not_forwarded(self):
        self.assertIsNone(session_diagnostic('Starting session with SessionId: private-fixture'))
        self.assertIsNone(session_diagnostic('unrecognized output with private-fixture'))


if __name__ == '__main__':
    unittest.main()
