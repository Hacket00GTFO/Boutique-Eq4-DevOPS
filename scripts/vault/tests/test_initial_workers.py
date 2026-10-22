from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from recover_initial_workers import recover, PreparationError

WINDOW = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
          'VAULT_AUTOMATION_READY': 'false'}
REPORT = {'startup': {'nodeadm-config': {'state': 'failed'},
                      'current_ec2_permission': {'describe_allowed': True}}}


class InitialWorkersTests(unittest.TestCase):
    def test_release_other_branch_and_activated_installations_never_restart(self):
        for changes in ({'GITHUB_REF': 'refs/heads/development'}, {'GITHUB_EVENT_NAME': 'push'},
                        {'VAULT_AUTOMATION_READY': 'true'}, {'VAULT_AUTOMATION_READY': ''}):
            read, inspect, repair = Mock(), Mock(), Mock()
            with self.subTest(changes=changes), self.assertRaises(SafeError):
                recover(dict(WINDOW, **changes), read, inspect, repair)
            read.assert_not_called()
            inspect.assert_not_called()
            repair.assert_not_called()

    def test_registered_worker_prevents_inspection_and_reboot(self):
        inspect, repair = Mock(), Mock()
        recover(WINDOW, lambda: {'items': [{}]}, inspect, repair)
        inspect.assert_not_called()
        repair.assert_not_called()

    def test_only_known_failed_state_with_current_permission_reaches_guarded_repair(self):
        repair = Mock()
        recover(WINDOW, lambda: {'items': []}, lambda: REPORT, repair)
        repair.assert_called_once_with(REPORT)
        for startup in ({'nodeadm-config': {'state': 'active'}},
                        {'nodeadm-config': {'state': 'failed'}, 'current_ec2_permission': {'describe_allowed': False}}):
            repair = Mock()
            try:
                recover(WINDOW, lambda: {'items': []}, lambda: {'startup': startup}, repair)
            except SafeError:
                self.assertEqual(startup['nodeadm-config']['state'], 'failed')
            repair.assert_not_called()

    def test_independent_repair_guard_failure_is_not_ignored(self):
        repair = Mock(side_effect=PreparationError('Application namespace exists'))
        with self.assertRaises(PreparationError):
            recover(WINDOW, lambda: {'items': []}, lambda: REPORT, repair)
