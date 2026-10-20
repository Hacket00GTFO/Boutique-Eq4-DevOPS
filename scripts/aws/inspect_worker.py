"""Read startup diagnostics through SSM from the one owned EKS storage worker."""
import json
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


def main():
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
            print(json.dumps({'instance': target, 'startup': json.loads(result['StandardOutputContent'])}, indent=2))
            return
        if result['Status'] in ('Failed', 'Cancelled', 'TimedOut'):
            raise PreparationError('The read-only startup probe failed; private command output suppressed.')
        time.sleep(2)
    raise PreparationError('Worker inspection did not finish; no startup settings were changed.')


if __name__ == '__main__':
    try:
        main()
    except PreparationError as error:
        sys.exit('Worker inspection stopped: ' + str(error))
    except (OSError, ValueError, KeyError):
        sys.exit('Worker inspection stopped; no private response was logged.')
