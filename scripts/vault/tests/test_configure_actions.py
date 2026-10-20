"""Credential transport and activation ordering without access to external services."""
import base64
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import configure_actions as actions
from common import SafeError


class ConfigureActionsTests(unittest.TestCase):
    def test_cli_failure_does_not_include_credentials_or_response_details(self):
        sensitive = 'isolated-secret-fixture'
        result = subprocess.CompletedProcess(['gh'], 1, sensitive, sensitive)
        with patch.object(actions.subprocess, 'run', return_value=result):
            with self.assertRaises(SafeError) as raised:
                actions.command(['gh', 'secret', 'set', 'AWS_SESSION_TOKEN'], sensitive)
        self.assertNotIn(sensitive, str(raised.exception))

    def test_secrets_use_stdin_and_explicit_environment_scope(self):
        sensitive = 'isolated-secret-fixture'
        with patch.object(actions, 'command') as command:
            actions.set_value('owner/repo', 'secret', 'VAULT_CI_TOKEN', sensitive, environment=True)
        args, value = command.call_args.args
        self.assertEqual(value, sensitive)
        self.assertNotIn(sensitive, args)
        self.assertEqual(args, ['gh', 'secret', 'set', 'VAULT_CI_TOKEN', '--repo',
                                'owner/repo', '--env', 'vault-lab'])

    def test_activation_follows_every_secret_and_trust_setting(self):
        credentials = {name: 'fixture-' + name for name in actions.AWS_KEYS}
        with patch.object(actions, 'prepare_environment') as prepare, \
                patch.object(actions, 'set_value') as write:
            actions.publish('owner/repo', 'i-12345678', credentials, b'public-certificate',
                            {'client_token': 'fixture-ci'}, {})
        prepare.assert_called_once_with('owner/repo', {})
        expected = []
        for name, value in credentials.items():
            expected += [call('owner/repo', 'secret', name, value),
                         call('owner/repo', 'secret', name, value, environment=True)]
        expected += [
            call('owner/repo', 'secret', 'VAULT_CI_TOKEN', 'fixture-ci', environment=True),
            call('owner/repo', 'variable', 'VAULT_INSTANCE_ID', 'i-12345678', environment=True),
            call('owner/repo', 'variable', 'VAULT_CA_CERT_B64',
                 base64.b64encode(b'public-certificate').decode(), environment=True),
            call('owner/repo', 'variable', 'ENABLE_VAULT_LAB', 'true'),
        ]
        self.assertEqual(write.call_args_list, expected)

    def test_upload_failure_does_not_activate_vault_job(self):
        with patch.object(actions, 'prepare_environment'), \
                patch.object(actions, 'set_value', side_effect=SafeError('Upload failed')) as write:
            with self.assertRaises(SafeError):
                actions.publish('owner/repo', 'i-12345678', {'AWS_ACCESS_KEY_ID': 'fixture'},
                                b'public-certificate', {'client_token': 'fixture-ci'}, {})
        self.assertFalse(any(c.args[2] == 'ENABLE_VAULT_LAB' for c in write.call_args_list))

    def test_existing_review_or_timer_protection_is_not_replaced(self):
        for rule in ('required_reviewers', 'wait_timer', 'custom'):
            with self.subTest(rule=rule), patch.object(actions, 'api') as api:
                with self.assertRaises(SafeError):
                    actions.prepare_environment('owner/repo', {'protection_rules': [{'type': rule}]})
                api.assert_not_called()

    def test_other_branch_rules_are_rejected_before_mutation(self):
        current = {'deployment_branch_policy': {'custom_branch_policies': True}}
        with patch.object(actions, 'api', return_value={
                'branch_policies': [{'name': 'development', 'type': 'branch'}]}) as api:
            with self.assertRaises(SafeError):
                actions.prepare_environment('owner/repo', current)
        self.assertEqual(len(api.call_args_list), 1)
        self.assertEqual(len(api.call_args.args), 2)

    def test_unrestricted_environment_is_enabled_before_querying_branch_rules(self):
        with patch.object(actions, 'api', side_effect=[{}, {'branch_policies': []}, {}]) as api:
            actions.prepare_environment('owner/repo', {})
        self.assertEqual(api.call_args_list, [
            call('owner/repo', 'environments/vault-lab', 'PUT', {'deployment_branch_policy': {
                'protected_branches': False, 'custom_branch_policies': True}}),
            call('owner/repo', 'environments/vault-lab/deployment-branch-policies?per_page=100'),
            call('owner/repo', 'environments/vault-lab/deployment-branch-policies', 'POST',
                 {'name': 'main', 'type': 'branch'}),
        ])

    def test_existing_main_rule_is_not_duplicated(self):
        current = {'deployment_branch_policy': {'custom_branch_policies': True}}
        with patch.object(actions, 'api', side_effect=[{
                'branch_policies': [{'name': 'main', 'type': 'branch'}]}, {}]) as api:
            actions.prepare_environment('owner/repo', current)
        self.assertEqual([c.args[2] for c in api.call_args_list if len(c.args) > 2], ['PUT'])


if __name__ == '__main__':
    unittest.main()
