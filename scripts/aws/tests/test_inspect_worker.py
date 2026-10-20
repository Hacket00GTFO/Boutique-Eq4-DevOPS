from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspect_worker import restart_unjoined_workers, PreparationError


class WorkerRecoveryTests(unittest.TestCase):
    def report(self):
        return {'instance': 'i-storage', 'startup': {'current_ec2_permission': {'describe_allowed': True},
                                                    'nodeadm-config': {'state': 'failed'}}}

    def test_current_denial_cannot_trigger_reboots(self):
        report = self.report()
        report['startup']['current_ec2_permission']['describe_allowed'] = False
        execute, call = Mock(), Mock()
        with self.assertRaises(PreparationError):
            restart_unjoined_workers(report, call=call, execute=execute)
        execute.assert_not_called()
        call.assert_not_called()

    def test_joined_nodes_or_application_namespace_block_automatic_reboot(self):
        for responses in (['context', '{"items":[{}]}'], ['context', '{"items":[]}', '{"metadata":{"name":"boutique-vault-lab"}}']):
            execute = Mock(side_effect=[subprocess.CompletedProcess([], 0, value, '') for value in responses])
            call = Mock()
            with self.subTest(kind='not empty'), self.assertRaises(PreparationError):
                restart_unjoined_workers(self.report(), call=call, execute=execute)
            call.assert_not_called()
