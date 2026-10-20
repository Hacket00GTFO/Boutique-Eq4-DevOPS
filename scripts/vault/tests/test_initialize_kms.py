import base64
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import seed_values
from common import SafeError
import initialize_kms


class FreshKmsInitializationTests(unittest.TestCase):
    def data(self):
        return {'values': seed_values(), 'pgp_public_keys': [base64.b64encode(bytes([n])).decode() for n in (1, 2, 3)],
                'repo': 'Hacket00GTFO/Boutique-Eq4-DevOPS', 'repository_id': '1333737803', 'owner_id': '181441407'}

    def test_initialized_or_shamir_vault_is_never_reinitialized(self):
        for responses in ([(200, {'initialized': True})],
                          [(200, {'initialized': False}), (200, {'type': 'shamir'})]):
            vault = Mock()
            vault.request.side_effect = responses
            with patch.object(initialize_kms, 'Vault', return_value=vault), self.assertRaises(SafeError):
                initialize_kms.initialize(self.data(), '/unused')
            self.assertFalse(any(call.args[0] == 'PUT' for call in vault.request.call_args_list))

    def test_native_init_encrypts_custody_preserves_admin_and_revokes_root(self):
        data = self.data()
        anonymous, root, operator = Mock(), Mock(), Mock()
        anonymous.request.side_effect = [(200, {'initialized': False}), (200, {'type': 'awskms'}),
                                         (200, {'root_token': 'private-root-value', 'recovery_keys_base64': ['encrypted-share'] * 3})]
        def request(method, path, value=None):
            if path == 'auth/token/create-orphan':
                return 200, {'auth': {'client_token': 'private-operator-value'}}
            if path == initialize_kms.DATA_PATH and method == 'GET':
                return 200, {'data': {'data': data['values']}}
            return 204, {}
        root.request.side_effect = request
        def encrypt(args, value=None):
            if '--with-colons' in args:
                return b'fpr:::::::::1234567890ABCDEF1234567890ABCDEF12345678:\n'
            return b'encrypted-public-key-output'
        original_temporary = tempfile.TemporaryDirectory
        with original_temporary() as directory:
            custody = Path(directory) / 'custody'
            with (patch.object(initialize_kms, 'Vault', side_effect=[anonymous, root, operator]),
                  patch.object(initialize_kms, 'gpg', side_effect=encrypt),
                  patch.object(initialize_kms.tempfile, 'TemporaryDirectory',
                               side_effect=lambda **kwargs: original_temporary(dir=directory)),
                  patch.object(initialize_kms, 'configure_base'), patch.object(initialize_kms, 'configure') as oidc,
                  patch.object(initialize_kms, 'runtime'),
                  patch.object(initialize_kms, 'snapshot', side_effect=lambda client, path: path.write_bytes(b'sealed-raft-data')),
                  patch.object(initialize_kms.subprocess, 'run', return_value=Mock(returncode=0)),
                  contextlib.redirect_stdout(io.StringIO())):
                initialize_kms.initialize(data, custody)
            material = json.loads((custody / 'init.json').read_text())
            self.assertTrue(material['root_token_revoked'])
            self.assertNotIn('private-root-value', (custody / 'init.json').read_text())
            init = anonymous.request.call_args_list[-1].args
            self.assertEqual(init[2]['recovery_threshold'], 2)
            self.assertEqual(init[2]['recovery_pgp_keys'], data['pgp_public_keys'])
            self.assertTrue(any(call.args[:2] == ('POST', 'auth/token/revoke-self') for call in root.request.call_args_list))
            oidc.assert_called_once_with(root, data['repo'], data['repository_id'], data['owner_id'])
            self.assertTrue((custody / 'vault-custody.tar.gpg').is_file())

    def test_verified_initialization_can_resume_without_reissuing_root_or_overwriting_values(self):
        import os
        data = self.data()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            record = {field: True for field in ('kms_restart_verified', 'tls_verified', 'oidc_configured', 'root_token_revoked')}
            record.update({field: data[field] for field in ('repo', 'repository_id', 'owner_id')})
            (path / 'verified.json').write_text(json.dumps(record))
            (path / 'verified.json').chmod(0o600)
            (path / 'vault-custody.tar.gpg').write_bytes(b'encrypted custody')
            vault = Mock()
            vault.request.return_value = (200, {'initialized': True})
            with (patch.object(initialize_kms, 'Vault', return_value=vault),
                  patch.object(initialize_kms, 'runtime') as runtime,
                  contextlib.redirect_stdout(io.StringIO())):
                initialize_kms.initialize(data, path)
            runtime.assert_called_once_with(vault)
            self.assertEqual(vault.request.call_count, 1)
            record['repo'] = 'other/project'
            (path / 'verified.json').write_text(json.dumps(record))
            with patch.object(initialize_kms, 'Vault', return_value=vault), self.assertRaises(SafeError):
                initialize_kms.initialize(data, path)
