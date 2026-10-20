"""Read startup diagnostics through SSM from the one owned EKS storage worker."""
import json
import argparse
import re
import subprocess
import sys
import time

from prepare_vault_kms import ACCOUNT, PreparationError, aws

PROBE = r'''
import json,re,subprocess,shutil
result={}
def run(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=25,check=False).stdout.strip()
for unit in ('nodeadm-config','nodeadm-run','kubelet','containerd'):
    state=run(['systemctl','is-active',unit])
    journal=run(['journalctl','-u',unit,'--no-pager','-n','100','--output','cat'])
    lines=[]
    for line in journal.splitlines():
        if re.search('error|failed|unauthor|forbidden|certificate|credential|bootstrap|config|metadata',line,re.I):
            line=re.sub(r'[A-Za-z0-9+/=_-]{80,}|(?:AKIA|ASIA)[A-Z0-9]{16}','[redacted]',line)
            lines.append(line[:400])
    result[unit]={'state':state,'diagnostics':lines[-12:]}
if shutil.which('aws'):
    check=subprocess.run(['aws','ec2','describe-instances','--region','us-east-1','--instance-ids',TARGET_ID,
                          '--query','Reservations[].Instances[].InstanceId','--output','json','--no-cli-pager'],
                         capture_output=True,text=True,timeout=30,check=False)
    result['current_ec2_permission']={'describe_allowed':check.returncode==0}
    if check.returncode:
        match=re.search(r'An error occurred \(([A-Za-z0-9_.-]+)\)',check.stderr)
        result['current_ec2_permission']['error_code']=match.group(1) if match else 'request_failed'
else:
    result['current_ec2_permission']={'aws_cli_available':False}
print(json.dumps(result))
'''


def restart_unjoined_workers(report, call=aws, execute=subprocess.run):
    startup = report['startup']
    if not startup.get('current_ec2_permission', {}).get('describe_allowed') or startup['nodeadm-config']['state'] != 'failed':
        raise PreparationError('Recover startup only after the previous denial is resolved and nodeadm is failed.')
    def local(args):
        result = execute(args, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            raise PreparationError('Cannot verify the empty cluster before worker recovery.')
        return result.stdout
    local(['aws', 'eks', 'update-kubeconfig', '--name', 'boutique-eq4-eks', '--region', 'us-east-1'])
    if json.loads(local(['kubectl', 'get', 'nodes', '-o', 'json'])).get('items'):
        raise PreparationError('Workers have joined the cluster; use the controlled workload maintenance procedure.')
    if local(['kubectl', 'get', 'namespace', 'boutique-vault-lab', '--ignore-not-found', '-o', 'json']).strip():
        raise PreparationError('Application namespace exists; do not reboot its workers automatically.')
    ids = set()
    for suffix in ('nodes', 'storage'):
        node = call('eks', 'describe-nodegroup', '--cluster-name', 'boutique-eq4-eks',
                    '--nodegroup-name', 'boutique-eq4-eks-' + suffix)['nodegroup']
        role = node['nodeRole']
        if (suffix == 'storage' and role != f'arn:aws:iam::{ACCOUNT}:role/LabRole') or (
                suffix == 'nodes' and not re.fullmatch(f'arn:aws:iam::{ACCOUNT}:role/.*-LabEksNodeRole-.*', role)):
            raise PreparationError('An expected worker role changed; recovery stopped.')
        for group in node['resources']['autoScalingGroups']:
            scaling = call('autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', group['name'])
            ids.update(item['InstanceId'] for entry in scaling['AutoScalingGroups'] for item in entry['Instances'])
    if len(ids) != 3 or report['instance'] not in ids:
        raise PreparationError('Recover exactly the three known workers, including the inspected storage node.')
    machines = call('ec2', 'describe-instances', '--instance-ids', *sorted(ids))
    actual = [item for entry in machines['Reservations'] for item in entry['Instances']]
    if {item['InstanceId'] for item in actual} != ids or any(item['State']['Name'] != 'running' for item in actual):
        raise PreparationError('Worker identity/state changed; no reboot requested.')
    call('ec2', 'reboot-instances', '--instance-ids', *sorted(ids))
    print('PASS: reboot requested for the three owned unjoined workers; all volumes and Vault were retained.')


def main(restart=False):
    if aws('sts', 'get-caller-identity')['Account'] != ACCOUNT:
        raise PreparationError('Worker inspection requires the configured account.')
    node = aws('eks', 'describe-nodegroup', '--cluster-name', 'boutique-eq4-eks',
               '--nodegroup-name', 'boutique-eq4-eks-storage')['nodegroup']
    if node['nodeRole'] != f'arn:aws:iam::{ACCOUNT}:role/LabRole':
        raise PreparationError('The storage worker role differs from the intended Lab role.')
    groups = node['resources']['autoScalingGroups']
    if len(groups) != 1:
        raise PreparationError('The storage worker group is ambiguous.')
    scaling = aws('autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', groups[0]['name'])
    instances = [item['InstanceId'] for group in scaling['AutoScalingGroups'] for item in group['Instances']]
    if len(instances) != 1:
        raise PreparationError('Inspect exactly the owned single storage worker.')
    target = instances[0]
    status = aws('ssm', 'describe-instance-information', '--filters', 'Key=InstanceIds,Values=' + target)
    if not any(item.get('PingStatus') == 'Online' for item in status.get('InstanceInformationList', [])):
        raise PreparationError('The storage worker is not online through SSM.')
    response = aws('ssm', 'send-command', '--instance-ids', target, '--document-name', 'AWS-RunShellScript',
                   '--parameters', json.dumps({'commands': ["python3 - <<'PY'\nTARGET_ID=" + json.dumps(target) + '\n' + PROBE + '\nPY'], 'executionTimeout': ['90']}),
                   '--comment', 'Read owned EKS worker startup service diagnostics')
    command_id = response['Command']['CommandId']
    for _ in range(30):
        try:
            result = aws('ssm', 'get-command-invocation', '--command-id', command_id, '--instance-id', target)
        except PreparationError as error:
            if '(InvocationDoesNotExist)' not in str(error):
                raise
            time.sleep(2)
            continue
        if result['Status'] == 'Success':
            report = {'instance': target, 'startup': json.loads(result['StandardOutputContent'])}
            print(json.dumps(report, indent=2))
            if restart:
                restart_unjoined_workers(report)
            return
        if result['Status'] in ('Failed', 'Cancelled', 'TimedOut'):
            raise PreparationError('The read-only startup probe failed; private command output suppressed.')
        time.sleep(2)
    raise PreparationError('Worker inspection did not finish; no startup settings were changed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart-unjoined-workers', action='store_true')
    try:
        main(parser.parse_args().restart_unjoined_workers)
    except PreparationError as error:
        sys.exit('Worker inspection stopped: ' + str(error))
    except (OSError, ValueError, KeyError):
        sys.exit('Worker inspection stopped; no private response was logged.')
