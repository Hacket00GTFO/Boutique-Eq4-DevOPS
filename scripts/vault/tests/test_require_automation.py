from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from require_automation import configuration, cloud, runtime

VALUES = {'DATA_RECOVERY_VERIFIED': 'true', 'VAULT_AUTOMATION_READY': 'true',
          'VAULT_INSTANCE_ID': 'i-12345678', 'VAULT_SSM_BUCKET': 'dedicated-transfer-bucket',
          'VAULT_KMS_KEY_ARN': 'arn:aws:kms:us-east-1:716142453772:key/12345678-1234-1234-1234-123456789012'}


class AutomationRequirementsTests(unittest.TestCase):
    def test_missing_recovery_or_security_configuration_blocks_before_cloud_access(self):
        for name in VALUES:
            invalid = dict(VALUES)
            invalid.pop(name)
            call = Mock()
            with self.subTest(name=name), self.assertRaises(SafeError):
                cloud(invalid, call)
            call.assert_not_called()

    def test_wrong_account_and_disabled_kms_cannot_deploy(self):
        call = Mock(return_value={'Account': '123456789012'})
        with self.assertRaisesRegex(SafeError, 'account'):
            cloud(VALUES, call)
        self.assertEqual(call.call_count, 1)
        metadata = {'Arn': VALUES['VAULT_KMS_KEY_ARN'], 'KeyState': 'Disabled',
                    'KeyUsage': 'ENCRYPT_DECRYPT', 'KeySpec': 'SYMMETRIC_DEFAULT'}
        call = Mock(side_effect=[{'Account': '716142453772'}, {'cluster': {'status': 'ACTIVE'}},
                                 {'KeyMetadata': metadata}])
        with self.assertRaisesRegex(SafeError, 'KMS key'):
            cloud(VALUES, call)

    def test_shamir_and_incomplete_migration_fail_the_runtime_gate(self):
        for status in ({'type': 'shamir'}, {'type': 'awskms', 'migration': True}):
            vault = Mock()
            vault.request.return_value = (200, status)
            with self.subTest(status=status), self.assertRaisesRegex(SafeError, 'native AWS KMS'):
                runtime(vault)

    def test_tls_or_sealed_failure_stops_before_any_secret_read(self):
        vault = Mock()
        vault.ready.side_effect = SafeError('TLS verification failed')
        with self.assertRaises(SafeError):
            runtime(vault)
        vault.request.assert_not_called()
