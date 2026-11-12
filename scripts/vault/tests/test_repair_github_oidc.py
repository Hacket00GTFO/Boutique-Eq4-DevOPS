"""Temporary privileged sessions must close even when a repair fails."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
import repair_github_oidc


class OidcRepairTests(unittest.TestCase):
    def session(self, *, bad_operator=False):
        events = []
        root, operator = Mock(), Mock()
        def root_call(method, path, payload=None):
            events.append(('root', path))
            if path == 'auth/token/lookup-self':
                return 200, {'data': {'policies': ['root']}}
            if path == 'auth/token/create-orphan':
                self.assertEqual(payload['policies'], ['boutique-operator'])
                self.assertTrue(payload['no_default_policy'])
                self.assertEqual(payload['explicit_max_ttl'], '15m')
                return 200, {'auth': {'client_token': 'isolated-operator'}}
            return 204, {}
        def operator_call(method, path, payload=None):
            events.append(('operator', path))
            return 200, {'data': {'policies': ['root'] if bad_operator else ['boutique-operator'],
                                  'ttl': 900, 'explicit_max_ttl': 900, 'renewable': False}}
        root.request.side_effect = root_call
        operator.request.side_effect = operator_call
        return root, operator, events

    def test_root_is_revoked_before_configuring_and_operator_afterwards(self):
        root, operator, events = self.session()
        def configure(*args):
            self.assertIs(args[0], operator)
            events.append(('configure', 'role'))
        with patch.object(repair_github_oidc, 'configure', side_effect=configure), redirect_stdout(io.StringIO()):
            repair_github_oidc.repair(root, 'owner/repo', 123, 456, client=lambda _: operator)
        self.assertLess(events.index(('root', 'auth/token/revoke-self')), events.index(('configure', 'role')))
        self.assertEqual(events[-1], ('operator', 'auth/token/revoke-self'))

    def test_failed_configuration_revokes_both_sessions(self):
        root, operator, events = self.session()
        with patch.object(repair_github_oidc, 'configure', side_effect=SafeError('Role conflict')), self.assertRaises(SafeError):
            repair_github_oidc.repair(root, 'owner/repo', 123, 456, client=lambda _: operator)
        self.assertIn(('root', 'auth/token/revoke-self'), events)
        self.assertEqual(events[-1], ('operator', 'auth/token/revoke-self'))

    def test_bad_operator_is_revoked_without_changing_the_role(self):
        root, operator, events = self.session(bad_operator=True)
        with patch.object(repair_github_oidc, 'configure') as configure, self.assertRaises(SafeError):
            repair_github_oidc.repair(root, 'owner/repo', 123, 456, client=lambda _: operator)
        configure.assert_not_called()
        self.assertIn(('root', 'auth/token/revoke-self'), events)
        self.assertEqual(events[-1], ('operator', 'auth/token/revoke-self'))

    def test_root_revoke_failure_closes_operator_and_refuses_the_change(self):
        root, operator, events = self.session()
        original = root.request.side_effect
        def request(method, path, payload=None):
            if path == 'auth/token/revoke-self':
                raise SafeError('Root revocation failed')
            return original(method, path, payload)
        root.request.side_effect = request
        with patch.object(repair_github_oidc, 'configure') as configure, self.assertRaises(SafeError):
            repair_github_oidc.repair(root, 'owner/repo', 123, 456, client=lambda _: operator)
        configure.assert_not_called()
        self.assertEqual(events[-1], ('operator', 'auth/token/revoke-self'))
