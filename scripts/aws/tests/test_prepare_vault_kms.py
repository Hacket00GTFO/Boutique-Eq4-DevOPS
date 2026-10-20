import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_vault_kms import ACCOUNT, PREFIX, PreparationError, prepare

KEY = PREFIX + '12345678-1234-1234-1234-123456789012'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/LabRole'
METADATA = {'Arn': KEY, 'KeyManager': 'CUSTOMER', 'KeyState': 'Enabled',
            'KeyUsage': 'ENCRYPT_DECRYPT', 'KeySpec': 'SYMMETRIC_DEFAULT'}
POLICY = {'Policy': json.dumps({'Statement': [{'Effect': 'Allow', 'Principal': {'AWS': ROLE},
                                              'Action': ['kms:Encrypt', 'kms:Decrypt', 'kms:DescribeKey']}]})}
PRELUDE = [{'Account': ACCOUNT}, {'clusters': []},
           {'InstanceProfile': {'Roles': [{'Arn': ROLE}]}}]


class VaultKmsPreparationTests(unittest.TestCase):
    def run_prepare(self, call, existing=None):
        with contextlib.redirect_stdout(io.StringIO()), patch.dict('os.environ', {'GITHUB_OUTPUT': ''}):
            return prepare(existing, call)

    def test_wrong_account_stops_before_cloud_mutations(self):
        call = Mock(return_value={'Account': '123456789012'})
        with self.assertRaises(PreparationError):
            self.run_prepare(call)
        self.assertEqual(call.call_count, 1)

    def test_access_denied_is_terminal_and_does_not_create_more_keys(self):
        call = Mock(side_effect=PRELUDE + [PreparationError('kms:describe-key failed (AccessDeniedException).')])
        with self.assertRaisesRegex(PreparationError, 'AccessDenied'):
            self.run_prepare(call)
        self.assertEqual(call.call_count, 4)

    def test_existing_key_is_reused_without_creating_or_replacing_policy(self):
        call = Mock(side_effect=PRELUDE + [{'KeyMetadata': METADATA}, POLICY, {}])
        self.assertEqual(self.run_prepare(call, KEY), KEY)
        self.assertFalse(any(entry.args[:2] in (('kms', 'create-key'), ('kms', 'put-key-policy'))
                             for entry in call.call_args_list))

    def test_only_not_found_allows_creation_and_records_key_before_later_failure(self):
        call = Mock(side_effect=PRELUDE + [PreparationError('(NotFoundException)'),
                                         {'KeyMetadata': METADATA}, PreparationError('(AccessDeniedException)')])
        with patch('prepare_vault_kms.record') as record:
            with self.assertRaises(PreparationError):
                self.run_prepare(call)
            record.assert_called_once_with(KEY)
        self.assertEqual(sum(entry.args[:2] == ('kms', 'create-key') for entry in call.call_args_list), 1)

    def test_service_managed_key_and_missing_vault_grant_are_rejected(self):
        for responses in ([{'KeyMetadata': dict(METADATA, KeyManager='AWS')}],
                          [{'KeyMetadata': METADATA}, {'Policy': '{"Statement":[]}'}]):
            call = Mock(side_effect=PRELUDE + responses)
            with self.subTest(kind='unsupported'), self.assertRaises(PreparationError):
                self.run_prepare(call, KEY)
            self.assertFalse(any(entry.args[:2] == ('kms', 'enable-key-rotation')
                                 for entry in call.call_args_list))
