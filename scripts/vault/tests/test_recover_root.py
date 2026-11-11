"""Failure handling for the temporary recovery window; no cloud access."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from recover_root import encrypted_shares, recovery_window


class AccessState:
    def __init__(self):
        self.enabled = False
        self.changes = []

    def request(self, *args, **kwargs):
        return (200 if self.enabled else 403), {}

    def toggle(self, enabled):
        self.changes.append(enabled)
        self.enabled = enabled


class RecoveryWindowTests(unittest.TestCase):
    def test_selects_recovery_shares_for_fresh_kms_and_preserves_migrated_shares(self):
        recovery = ['encrypted-recovery-1', 'encrypted-recovery-2', 'encrypted-recovery-3']
        old = ['encrypted-old-1', 'encrypted-old-2', 'encrypted-old-3']
        kms = {'type': 'awskms', 'recovery_seal': True}
        self.assertEqual(encrypted_shares({'keys_base64': [], 'recovery_keys_base64': recovery}, kms), recovery)
        self.assertEqual(encrypted_shares({'keys_base64': old}, kms), old)
        self.assertEqual(encrypted_shares({'keys_base64': old, 'recovery_keys_base64': recovery}, kms), recovery)
        self.assertEqual(encrypted_shares({'keys_base64': old}, {'type': 'shamir'}), old)

    def test_invalid_custody_or_seal_never_opens_recovery(self):
        for material, seal in (
            ({'recovery_keys_base64': ['one']}, {'type': 'awskms', 'recovery_seal': True}),
            ({'recovery_keys_base64': ['one', '', 'three']}, {'type': 'awskms', 'recovery_seal': True}),
            ({'recovery_keys_base64': ['one', 'two', 'three']}, {'type': 'shamir'}),
            ({'keys_base64': ['one', 'two', 'three']}, {'type': 'awskms', 'recovery_seal': False}),
            ({'keys_base64': ['one', 'two', 'three']}, {'type': 'shamir', 'migration': True}),
        ):
            with self.subTest(seal=seal), self.assertRaises(SafeError):
                encrypted_shares(material, seal)

    def test_interrupt_closes_access(self):
        state = AccessState()
        with redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            with recovery_window(state, state.toggle):
                raise KeyboardInterrupt
        self.assertEqual(state.changes, [True, False])
        self.assertFalse(state.enabled)

    def test_partial_open_failure_still_closes_access(self):
        state = AccessState()

        def toggle(enabled):
            state.toggle(enabled)
            if enabled:
                raise SafeError('Simulated reload failure')

        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SafeError, 'reload failure'):
            with recovery_window(state, toggle):
                self.fail('Recovery must not start')
        self.assertEqual(state.changes, [True, False])
        self.assertFalse(state.enabled)

    def test_existing_access_is_not_overwritten(self):
        state = AccessState()
        state.enabled = True
        with self.assertRaisesRegex(SafeError, 'already exposed'):
            with recovery_window(state, state.toggle):
                self.fail('Recovery must not start')
        self.assertEqual(state.changes, [])

    def test_close_failure_does_not_report_success(self):
        state = AccessState()

        def toggle(enabled):
            if not enabled:
                raise SafeError('Simulated close failure')
            state.toggle(enabled)

        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(SafeError, '--close-only'):
            with recovery_window(state, toggle):
                pass
        self.assertNotIn('PASS', output.getvalue())


if __name__ == '__main__':
    unittest.main()
