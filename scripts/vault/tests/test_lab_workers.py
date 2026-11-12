"""Only a failed, empty registered-worker set with original storage can be rebooted."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from recover_lab_workers import recover

WINDOW = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'push',
          'DATA_RECOVERY_VERIFIED': 'true', 'VAULT_AUTOMATION_READY': 'true', 'VAULT_INSTANCE_ID': 'i-22222222'}
REPORT = {'instance': 'i-storage', 'startup': {
    'nodeadm-config': {'state': 'failed'}, 'nodeadm-run': {'state': 'inactive'},
    'kubelet': {'state': 'inactive'}, 'containerd': {'state': 'inactive'},
    'current_ec2_permission': {'describe_allowed': True}}}


class LabCloud:
    def __init__(self):
        self.calls = []
        self.members = {'nodes': ['i-app1', 'i-app2'], 'storage': ['i-storage']}
        self.role = {'nodes': 'arn:aws:iam::716142453772:role/cf-LabEksNodeRole-test',
                     'storage': 'arn:aws:iam::716142453772:role/LabRole'}
        self.account, self.vpc, self.state = '716142453772', 'vpc-owned', 'running'
        self.membership_reads = 0
        self.change_on_second_read = False

    def __call__(self, service, action, *args):
        self.calls.append((service, action, *args))
        if service == 'sts':
            return {'Account': self.account}
        if action == 'describe-cluster':
            return {'cluster': {'status': 'ACTIVE', 'resourcesVpcConfig': {'vpcId': 'vpc-owned'}}}
        if action == 'describe-nodegroup':
            suffix = args[-1].rsplit('-', 1)[-1]
            return {'nodegroup': {'status': 'ACTIVE', 'clusterName': 'boutique-eq4-eks',
                    'nodegroupName': args[-1], 'nodeRole': self.role[suffix],
                    'resources': {'autoScalingGroups': [{'name': suffix}]}}}
        if service == 'autoscaling':
            return {'AutoScalingGroups': [{'AutoScalingGroupName': args[-1], 'Instances': [
                {'InstanceId': instance, 'LifecycleState': 'InService'} for instance in self.members[args[-1]]]}]}
        if action == 'describe-instances':
            self.membership_reads += 1
            machines = [{'InstanceId': instance, 'State': {'Name': self.state}, 'VpcId': self.vpc}
                        for instance in args[1:]]
            if self.change_on_second_read and self.membership_reads == 2:
                machines.pop()
            return {'Reservations': [{'Instances': machines}]}
        if action == 'reboot-instances':
            return {}
        raise AssertionError('Unexpected AWS operation')

    def reboots(self):
        return [call for call in self.calls if call[:2] == ('ec2', 'reboot-instances')]


def storage(changed=False):
    marker = {'data': {'dump_sha256': 'a' * 64, 'admin_env_verified': 'true', 'pvc_uid': 'claim-1',
                       'volume_name': 'pv-1', 'volume_handle': 'vol-1'}}
    pvc = {'metadata': {'uid': 'claim-1'}, 'status': {'phase': 'Bound'},
           'spec': {'storageClassName': 'boutique-ebs-gp3', 'volumeName': 'pv-1'}}
    pv = {'spec': {'claimRef': {'uid': 'claim-1'},
                  'csi': {'driver': 'ebs.csi.aws.com', 'volumeHandle': 'vol-other' if changed else 'vol-1'}}}
    def query(*args):
        if args[:2] == ('get', 'configmap'):
            return json.dumps(marker)
        if args[:2] == ('get', 'pvc'):
            return json.dumps(pvc)
        if args[:2] == ('get', 'pv'):
            return json.dumps(pv)
        raise AssertionError('Worker recovery must only read storage metadata')
    return query


class LabWorkersTests(unittest.TestCase):
    def test_recovers_three_unregistered_workers_with_existing_verified_application(self):
        cloud = LabCloud()
        recover(WINDOW, read=lambda: {'items': []}, call=cloud, query=storage(), inspect=lambda: REPORT)
        self.assertEqual(cloud.reboots(), [('ec2', 'reboot-instances', '--instance-ids', 'i-app1', 'i-app2', 'i-storage')])
        self.assertTrue(all(action in {'get-caller-identity', 'describe-cluster', 'describe-nodegroup',
                                     'describe-auto-scaling-groups', 'describe-instances', 'reboot-instances'}
                            for _, action, *_ in cloud.calls))

    def test_other_events_branches_and_unverified_data_never_read_or_change_cloud(self):
        for change in ({'GITHUB_REF': 'refs/heads/development'}, {'GITHUB_EVENT_NAME': 'pull_request'},
                       {'DATA_RECOVERY_VERIFIED': 'false'}, {'VAULT_AUTOMATION_READY': 'false'}, {'VAULT_INSTANCE_ID': ''}):
            cloud, read, query, inspect = Mock(), Mock(), Mock(), Mock()
            with self.subTest(change=change), self.assertRaises(SafeError):
                recover(dict(WINDOW, **change), read, cloud, query, inspect)
            cloud.assert_not_called()
            read.assert_not_called()

    def test_registered_nodes_are_never_rebooted(self):
        cloud, query, inspect = Mock(), Mock(), Mock()
        recover(WINDOW, lambda: {'items': [{}]}, cloud, query, inspect)
        cloud.assert_not_called()
        query.assert_not_called()
        inspect.assert_not_called()

    def test_changed_volume_or_missing_evidence_stops_before_reboot(self):
        for query in (storage(changed=True), Mock(side_effect=SafeError('Evidence missing'))):
            cloud, inspect = LabCloud(), Mock()
            with self.assertRaises(SafeError):
                recover(WINDOW, lambda: {'items': []}, cloud, query, inspect)
            self.assertEqual(cloud.reboots(), [])
            inspect.assert_not_called()

    def test_current_denial_wrong_inspected_machine_and_active_services_never_reboot(self):
        variants = []
        for unit, field, value in (('current_ec2_permission', 'describe_allowed', False),
                                   ('kubelet', 'state', 'active'), ('containerd', 'state', 'active')):
            report = copy.deepcopy(REPORT)
            report['startup'][unit][field] = value
            variants.append(report)
        variants.append(dict(REPORT, instance='i-unrelated'))
        for report in variants:
            cloud = LabCloud()
            with self.subTest(report=report), self.assertRaises(SafeError):
                recover(WINDOW, lambda: {'items': []}, cloud, storage(), lambda: report)
            self.assertEqual(cloud.reboots(), [])

    def test_normal_boot_is_allowed_to_join_without_a_reboot(self):
        report = copy.deepcopy(REPORT)
        report['startup']['nodeadm-config']['state'] = 'activating'
        cloud = LabCloud()
        recover(WINDOW, lambda: {'items': []}, cloud, storage(), lambda: report)
        self.assertEqual(cloud.reboots(), [])

    def test_identity_state_count_network_and_membership_changes_stop_reboot(self):
        variants = []
        for field, value in (('account', '000000000000'), ('vpc', 'vpc-other'), ('state', 'stopping'),
                             ('change_on_second_read', True)):
            cloud = LabCloud()
            setattr(cloud, field, value)
            variants.append(cloud)
        cloud = LabCloud()
        cloud.role['storage'] = 'arn:aws:iam::716142453772:role/OtherRole'
        variants.append(cloud)
        cloud = LabCloud()
        cloud.members['nodes'].append('i-extra')
        variants.append(cloud)
        for cloud in variants:
            with self.subTest(calls=cloud.calls), self.assertRaises(SafeError):
                recover(WINDOW, lambda: {'items': []}, cloud, storage(), lambda: REPORT)
            self.assertEqual(cloud.reboots(), [])

    def test_worker_joining_during_probe_prevents_reboot(self):
        cloud = LabCloud()
        read = Mock(side_effect=[{'items': []}, {'items': [{}]}])
        recover(WINDOW, read, cloud, storage(), lambda: REPORT)
        self.assertEqual(cloud.reboots(), [])

    def test_vault_instance_is_never_a_worker_reboot_target(self):
        cloud, inspect = LabCloud(), Mock()
        cloud.members['storage'] = [WINDOW['VAULT_INSTANCE_ID']]
        with self.assertRaisesRegex(SafeError, 'Vault instance'):
            recover(WINDOW, lambda: {'items': []}, cloud, storage(), inspect)
        self.assertEqual(cloud.reboots(), [])
        inspect.assert_not_called()

    def test_storage_replaced_during_inspection_prevents_reboot(self):
        cloud = LabCloud()
        changed = False
        def inspect():
            nonlocal changed
            changed = True
            return REPORT
        def query(*args):
            return storage(changed=changed)(*args)
        with self.assertRaisesRegex(SafeError, 'verified recovery volume'):
            recover(WINDOW, lambda: {'items': []}, cloud, query, inspect)
        self.assertEqual(cloud.reboots(), [])
