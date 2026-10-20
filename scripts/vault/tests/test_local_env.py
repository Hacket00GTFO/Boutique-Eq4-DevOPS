from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from local_env import integration_values, verify_admin


class LocalEnvironmentTests(unittest.TestCase):
    def values(self, extra=''):
        return ('ADMIN_EMAIL=owner@example.invalid\nADMIN_PASSWORD=' + 'x' * 20 + '\n' +
                '\n'.join(name + '=' + 'p' * 32 for name in
                          ('APP_DB_PASSWORD', 'POSTGRES_PASSWORD', 'ADMIN_API_TOKEN',
                           'CATALOG_READ_TOKEN', 'CHECKOUT_API_TOKEN')) + '\n' + extra)

    def read(self, content):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text(content)
            return integration_values(path)

    def test_admin_is_preserved_and_compose_database_urls_are_derived(self):
        values = self.read(self.values())
        self.assertEqual(values['ADMIN_EMAIL'], 'owner@example.invalid')
        self.assertEqual(values['ADMIN_PASSWORD'], 'x' * 20)
        self.assertIn('boutique_app:' + 'p' * 32 + '@postgres:5432/boutique', values['DATABASE_URL'])

    def test_duplicate_and_missing_admin_are_rejected(self):
        for data in (self.values('ADMIN_EMAIL=other@example.invalid\n'),
                     self.values().replace('ADMIN_EMAIL=owner@example.invalid\n', '')):
            with self.subTest(data_type='invalid'), self.assertRaises(SafeError):
                self.read(data)

    def test_quoted_password_is_literal_and_url_encoded(self):
        data = self.values().replace('APP_DB_PASSWORD=' + 'p' * 32,
                                     'APP_DB_PASSWORD="literal$' + 'p' * 25 + '@"')
        values = self.read(data)
        self.assertEqual(values['APP_DB_PASSWORD'], 'literal$' + 'p' * 25 + '@')
        self.assertIn('%24', values['DATABASE_URL'])
        self.assertIn('%40', values['DATABASE_URL'])

    def test_host_mismatch_and_unmatched_quotes_are_rejected(self):
        for extra in ('DATABASE_URL=postgresql://bad:bad@external/boutique\n',
                      'MIGRATION_DATABASE_URL="unterminated\n'):
            with self.subTest(kind='invalid'), self.assertRaises(SafeError):
                self.read(self.values(extra))

    def test_inactive_wrong_password_or_non_admin_accounts_are_rejected(self):
        import hashlib
        values = self.read(self.values())
        digest = hashlib.pbkdf2_hmac('sha256', values['ADMIN_PASSWORD'].encode(), b'test-salt', 1000).hex()
        user = {'email': values['ADMIN_EMAIL'], 'active': True, 'role': 'admin',
                'password_hash': 'pbkdf2:sha256:1000$test-salt$' + digest}
        verify_admin(values, [user])
        for replacement in ({'active': False}, {'role': 'viewer'}, {'email': 'other@example.invalid'},
                            {'password_hash': 'pbkdf2:sha256:1000$test-salt$incorrect'}):
            with self.subTest(kind=list(replacement)), self.assertRaises(SafeError):
                verify_admin(values, [dict(user, **replacement)])
