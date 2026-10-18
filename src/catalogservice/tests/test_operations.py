"""Operations contracts against the isolated PostgreSQL database."""
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
import uuid
from werkzeug.security import generate_password_hash
from main import app
from db import pool


class OperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if '/boutique_test' not in os.environ['DATABASE_URL']:
            raise RuntimeError('Operations tests require boutique_test')
        cls.service = {'Authorization': 'Bearer ' + os.environ['ADMIN_API_TOKEN']}
        with app.test_client() as client:
            result = client.post('/api/v1/auth/login', headers=cls.service,
                                 json={'email': os.environ['ADMIN_EMAIL'], 'password': os.environ['ADMIN_PASSWORD']})
            assert result.status_code == 200
            cls.session = dict(cls.service, **{'X-Admin-Session': result.json['token']})

    def setUp(self):
        self.client = app.test_client()
        self.run_id = str(uuid.uuid4())

    def tearDown(self):
        with pool.connection() as conn:
            conn.execute('DELETE FROM boutique.operations_reports WHERE run_id=%s', (self.run_id,))

    def report(self, sequence=1, status='running'):
        return {'kind': 'ansible', 'run_id': self.run_id, 'sequence': sequence, 'report': {
            'playbook': 'site.yml', 'status': status, 'started_at': datetime.now(timezone.utc).isoformat(),
            'elapsed': 3, 'counters': {'ok': 1, 'changed': 0, 'failed': 0, 'unreachable': 0, 'skipped': 0},
            'events': [{'type': 'ok', 'label': 'Install Vault', 'host': 'i-0123456789', 'elapsed': 3}]}}

    def send(self, report):
        return self.client.post('/api/v1/operations/reports', headers=self.service, json=report)

    def test_reports_require_service_auth_and_reads_require_admin_session(self):
        self.assertEqual(self.client.post('/api/v1/operations/reports', json=self.report()).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/operations/ansible').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/operations/ansible', headers=self.service).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/operations/vault', headers=self.session).status_code, 200)

    def test_viewer_cannot_read_operations(self):
        user = uuid.uuid4()
        email = str(user) + '@example.invalid'
        with pool.connection() as conn:
            conn.execute('INSERT INTO boutique.admin_users(id,email,password_hash,role) VALUES (%s,%s,%s,%s)',
                         (user, email, generate_password_hash('viewer-test-password'), 'viewer'))
        try:
            result = self.client.post('/api/v1/auth/login', headers=self.service, json={'email': email, 'password': 'viewer-test-password'})
            headers = dict(self.service, **{'X-Admin-Session': result.json['token']})
            self.assertEqual(self.client.get('/api/v1/operations/vault', headers=headers).status_code, 403)
            self.assertEqual(self.client.get('/api/v1/operations/ansible', headers=headers).status_code, 403)
        finally:
            with pool.connection() as conn:
                conn.execute('DELETE FROM boutique.sessions WHERE user_id=%s', (user,))
                conn.execute('DELETE FROM boutique.admin_users WHERE id=%s', (user,))

    def test_terminal_updates_are_monotonic_and_terminal_result_is_immutable(self):
        self.assertTrue(self.send(self.report()).json['accepted'])
        self.assertFalse(self.send(self.report()).json['accepted'])
        self.assertTrue(self.send(self.report(2, 'succeeded')).json['accepted'])
        self.assertFalse(self.send(self.report(3, 'failed')).json['accepted'])
        result = self.client.get('/api/v1/operations/ansible?run_id=' + self.run_id, headers=self.session)
        self.assertEqual(result.json['selected']['status'], 'succeeded')
        self.assertEqual(result.json['selected']['events'][0]['label'], 'Install Vault')
        self.assertNotIn('events', next(r for r in result.json['runs'] if r['id'] == self.run_id))

    def test_raw_stdout_and_protected_fields_are_not_persisted(self):
        report = self.report()
        report['report']['stdout'] = 'SENTINEL_PRIVATE_RESULT'
        report['report']['events'][0]['stdout'] = 'SENTINEL_PRIVATE_RESULT'
        report['report']['events'][0]['label'] = 'password=EXAMPLE_PRIVATE_PASSWORD hvs.EXAMPLE_PRIVATE_TOKEN'
        self.assertEqual(self.send(report).status_code, 202)
        with pool.connection() as conn:
            payload = conn.execute('SELECT payload FROM boutique.operations_reports WHERE run_id=%s', (self.run_id,)).fetchone()['payload']
        raw = json.dumps(payload)
        self.assertNotIn('EXAMPLE_PRIVATE_PASSWORD', raw)
        self.assertNotIn('EXAMPLE_PRIVATE_TOKEN', raw)
        self.assertNotIn('SENTINEL_PRIVATE_RESULT', raw)

    def test_vault_health_unknown_and_stale_are_explicit(self):
        report = {'kind': 'vault', 'run_id': self.run_id, 'sequence': 1,
                  'report': {'reachable': False, 'initialized': None, 'sealed': None, 'standby': None, 'version': '', 'http_status': 0}}
        self.assertEqual(self.send(report).status_code, 202)
        with pool.connection() as conn:
            conn.execute("UPDATE boutique.operations_reports SET received_at=now()-interval '2 minutes' WHERE run_id=%s", (self.run_id,))
        snapshot = self.client.get('/api/v1/operations/vault', headers=self.session).json['snapshot']
        self.assertTrue(snapshot['stale'])
        self.assertFalse(snapshot['reachable'])
        self.assertIsNone(snapshot['sealed'])

    def test_invalid_reports_are_rejected(self):
        for mutation in ('playbook', 'status', 'events', 'future', 'sequence'):
            report = self.report()
            if mutation == 'playbook': report['report']['playbook'] = '../../shell.sh'
            if mutation == 'status': report['report']['status'] = 'arbitrary-command'
            if mutation == 'events': report['report']['events'] *= 401
            if mutation == 'future': report['report']['started_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            if mutation == 'sequence': report['sequence'] = 0
            with self.subTest(mutation=mutation):
                self.assertEqual(self.send(report).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/operations/ansible?run_id=../../etc', headers=self.session).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/operations/ansible?run_id=' + str(uuid.uuid4()), headers=self.session).status_code, 404)
