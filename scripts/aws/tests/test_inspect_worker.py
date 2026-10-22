from pathlib import Path
import copy
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

    def responses(self):
        return [
            {'nodegroup': {'nodeRole': 'arn:aws:iam::716142453772:role/cf-LabEksNodeRole-test',
                           'resources': {'autoScalingGroups': [{'name': 'app-workers'}]}}},
            {'AutoScalingGroups': [{'Instances': [{'InstanceId': 'i-app1'}, {'InstanceId': 'i-app2'}]}]},
            {'nodegroup': {'nodeRole': 'arn:aws:iam::716142453772:role/LabRole',
                           'resources': {'autoScalingGroups': [{'name': 'storage-worker'}]}}},
            {'AutoScalingGroups': [{'Instances': [{'InstanceId': 'i-storage'}]}]},
            {'Reservations': [{'Instances': [{'InstanceId': instance, 'State': {'Name': 'running'}}
                                            for instance in ('i-app1', 'i-app2', 'i-storage')]}]},
            {},
        ]

    def empty_cluster(self):
        return Mock(side_effect=[subprocess.CompletedProcess([], 0, output, '')
                                 for output in ('context', '{"items":[]}', '')])

    def test_reboots_only_three_verified_unjoined_workers_without_touching_volumes(self):
        call = Mock(side_effect=self.responses())
        restart_unjoined_workers(self.report(), call=call, execute=self.empty_cluster())
        self.assertEqual(call.call_args.args,
                         ('ec2', 'reboot-instances', '--instance-ids', 'i-app1', 'i-app2', 'i-storage'))
        self.assertEqual(call.call_count, 6)

    def test_changed_roles_instance_set_or_state_never_reboot(self):
        variants = []
        wrong_role = self.responses()
        wrong_role[2]['nodegroup']['nodeRole'] = 'arn:aws:iam::716142453772:role/OtherRole'
        variants.append(wrong_role)
        extra_worker = self.responses()
        extra_worker[1]['AutoScalingGroups'][0]['Instances'].append({'InstanceId': 'i-other'})
        variants.append(extra_worker)
        missing_storage = self.responses()
        missing_storage[3]['AutoScalingGroups'][0]['Instances'][0]['InstanceId'] = 'i-other'
        variants.append(missing_storage)
        not_running = self.responses()
        not_running[4]['Reservations'][0]['Instances'][0]['State']['Name'] = 'stopping'
        variants.append(not_running)
        missing_instance = self.responses()
        missing_instance[4]['Reservations'][0]['Instances'].pop()
        variants.append(missing_instance)
        for index, responses in enumerate(variants):
            call = Mock(side_effect=copy.deepcopy(responses))
            with self.subTest(index=index), self.assertRaises(PreparationError):
                restart_unjoined_workers(self.report(), call=call, execute=self.empty_cluster())
            self.assertFalse(any(args.args[:2] == ('ec2', 'reboot-instances') for args in call.call_args_list))
