"""Native Vault JWT verification and KMS seal migration against isolated services."""
import base64
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATA_PATH, SafeError, Vault, private_json
from github_oidc import ISSUER, role_configuration
import migrate_kms
from require_automation import runtime
import test_vault_live as fixtures


@unittest.skipUnless(os.environ.get('VAULT_TEST_BINARY'), 'Requires a real isolated Vault binary')
class NativeAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.LiveVaultTests.setUpClass.__func__(cls)

    @classmethod
    def stop(cls):
        fixtures.LiveVaultTests.stop.__func__(cls)

    def test_github_jwt_claims_and_policies_are_enforced_by_vault(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.root.request('POST', 'sys/auth/github', {'type': 'jwt'})
        self.root.request('POST', 'auth/github/config', {'jwt_validation_pubkeys': [public], 'bound_issuer': ISSUER})
        role = role_configuration('owner/repo', 123, 456)
        self.root.request('POST', 'auth/github/role/boutique-main', role)
        claims = {
            'iss': ISSUER, 'sub': role['bound_subject'], 'aud': role['bound_audiences'][0],
            'repository_id': '123', 'repository_owner_id': '456', 'ref': 'refs/heads/main',
            'workflow_ref': role['bound_claims']['workflow_ref'][0],
            'iat': int(time.time()) - 5, 'nbf': int(time.time()) - 5, 'exp': int(time.time()) + 300,
        }
        def token(values):
            def part(value):
                return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=')
            message = part({'alg': 'RS256', 'typ': 'JWT'}) + b'.' + part(values)
            signature = private.sign(message, padding.PKCS1v15(), hashes.SHA256())
            return (message + b'.' + base64.urlsafe_b64encode(signature).rstrip(b'=')).decode()
        anonymous = Vault()
        auth = anonymous.request('POST', 'auth/github/login', {'role': 'boutique-main', 'jwt': token(claims)})[1]['auth']
        self.assertEqual(auth['policies'], ['boutique-ci'])
        self.assertFalse(auth['renewable'])
        self.assertLessEqual(auth['lease_duration'], 900)
        client = Vault(auth['client_token'])
        self.assertEqual(client.request('GET', DATA_PATH)[1]['data']['data'], self.values)
        with self.assertRaisesRegex(SafeError, 'HTTP 403'):
            client.request('GET', 'sys/audit')
        for change in ({'ref': 'refs/heads/development'}, {'repository_id': '999'},
                       {'repository_owner_id': '999'}, {'sub': 'repo:owner/repo:pull_request'},
                       {'workflow_ref': 'owner/repo/.github/workflows/other.yaml@refs/heads/main'},
                       {'aud': 'another-audience'}, {'exp': int(time.time()) - 600}):
            with self.subTest(change=list(change)), self.assertRaises(SafeError):
                anonymous.request('POST', 'auth/github/login', {'role': 'boutique-main', 'jwt': token(dict(claims, **change))})

    def test_kms_migration_retains_data_and_restart_auto_unseals(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key_id = '12345678-1234-1234-1234-123456789012'
        arn = 'arn:aws:kms:us-east-1:123456789012:key/' + key_id
        cipher = AESGCM(AESGCM.generate_key(bit_length=256))
        operations = []
        class KMS(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                operation = self.headers['X-Amz-Target'].rsplit('.', 1)[-1]
                operations.append(operation)
                if operation == 'DescribeKey':
                    result = {'KeyMetadata': {'KeyId': key_id, 'Arn': arn, 'Enabled': True,
                              'KeyState': 'Enabled', 'KeyUsage': 'ENCRYPT_DECRYPT', 'KeySpec': 'SYMMETRIC_DEFAULT'}}
                elif operation == 'Encrypt':
                    nonce = os.urandom(12)
                    encrypted = nonce + cipher.encrypt(nonce, base64.b64decode(data['Plaintext']), None)
                    result = {'CiphertextBlob': base64.b64encode(encrypted).decode(), 'KeyId': arn,
                              'EncryptionAlgorithm': 'SYMMETRIC_DEFAULT'}
                elif operation == 'Decrypt':
                    encrypted = base64.b64decode(data['CiphertextBlob'])
                    result = {'Plaintext': base64.b64encode(cipher.decrypt(encrypted[:12], encrypted[12:], None)).decode(),
                              'KeyId': arn, 'EncryptionAlgorithm': 'SYMMETRIC_DEFAULT'}
                else:
                    self.send_error(400)
                    return
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-amz-json-1.1')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), KMS)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        config = json.loads(self.config_path.read_text())
        config['seal'] = {'awskms': {'region': 'us-east-1', 'kms_key_id': arn,
                                    'endpoint': 'http://127.0.0.1:' + str(server.server_port)}}
        environment = dict(os.environ, AWS_ACCESS_KEY_ID='isolated-access-key',
                           AWS_SECRET_ACCESS_KEY='isolated-secret-key', AWS_SESSION_TOKEN='isolated-session',
                           AWS_EC2_METADATA_DISABLED='true')
        restarts = []
        def restart():
            self.__class__.stop()
            self.config_path.write_text(json.dumps(config))
            self.__class__.process = subprocess.Popen(self.command, stdout=self.log, stderr=subprocess.STDOUT, env=environment)
            restarts.append(True)
        with tempfile.TemporaryDirectory(prefix='boutique-kms-custody-') as directory:
            snapshot = Path(directory) / 'before.snap'
            journal = Path(directory) / 'migration.json'
            migrate_kms.snapshot(self.root, snapshot)
            self.assertGreater(snapshot.stat().st_size, 32)
            state = {'phase': 'prepared', 'key': arn, 'instance': 'i-12345678',
                     'fingerprint': migrate_kms.fingerprint(self.root)}
            private_json(journal, state)
            with contextlib.redirect_stdout(io.StringIO()):
                migrate_kms.migrate(Vault(), self.root, self.shares[:2], state, journal, restart)
            self.assertEqual(json.loads(journal.read_text())['phase'], 'verified')
            self.assertEqual(len(restarts), 2)
            self.assertEqual(self.root.request('GET', DATA_PATH)[1]['data']['data'], self.values)
            self.assertTrue({'Encrypt', 'Decrypt', 'DescribeKey'}.issubset(operations))
            with contextlib.redirect_stdout(io.StringIO()):
                runtime(Vault())


if __name__ == '__main__':
    unittest.main()
