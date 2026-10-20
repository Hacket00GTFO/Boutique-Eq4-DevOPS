"""Direct AWS release using repository credentials and the existing Kubernetes Secret."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import sys

# Reuse the established transaction, rollback and functional smoke implementation.
# Importing it does not connect to Vault or require Vault configuration.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vault'))
import deploy as delivery
from common import SafeError, validate_values

ACCOUNT = '716142453772'
REGION = 'us-east-1'
CLUSTER = 'boutique-eq4-eks'
REGISTRY = ACCOUNT + '.dkr.ecr.' + REGION + '.amazonaws.com'
SERVICES = ('adservice', 'cartservice', 'catalogservice', 'checkoutservice',
            'currencyservice', 'emailservice', 'frontend', 'loadgenerator',
            'paymentservice', 'productcatalogservice', 'recommendationservice', 'shippingservice')


def aws(*args, timeout=60):
    result = subprocess.run(['aws', *args, '--region', REGION, '--no-cli-pager'],
                            capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode:
        if 'voc-cancel-cred' in result.stderr or 'ExpiredToken' in result.stderr:
            raise SafeError('Start the Learner Lab and update all three repository AWS secrets from the same active session.')
        if 'ResourceNotFoundException' in result.stderr or 'RepositoryNotFoundException' in result.stderr:
            if args[0] == 'eks':
                raise SafeError('The required EKS cluster boutique-eq4-eks is missing in account 716142453772 / us-east-1. AWS authentication passed; recover the existing infrastructure before deploying.')
            raise SafeError('One or more required ECR repositories are missing in account 716142453772 / us-east-1. AWS authentication passed; recover the existing infrastructure before publishing.')
        raise SafeError('AWS request failed; check the active Lab session and service permissions. Private output suppressed.')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def checked_cluster(call=aws):
    if call('sts', 'get-caller-identity')['Account'] != ACCOUNT:
        raise SafeError('Wrong AWS account. Use the three Learner Lab credentials for account ' + ACCOUNT + '.')
    cluster = call('eks', 'describe-cluster', '--name', CLUSTER)['cluster']
    if cluster['status'] != 'ACTIVE':
        raise SafeError('The existing EKS cluster must be ACTIVE before releasing.')
    return cluster


def preflight(call=aws):
    checked_cluster(call)
    expected = {'boutique-eq4/' + name for name in SERVICES}
    repositories = call('ecr', 'describe-repositories', '--repository-names', *sorted(expected))
    if {entry['repositoryName'] for entry in repositories['repositories']} != expected:
        raise SafeError('All twelve existing ECR repositories are required before releasing.')
    print('PASS: correct AWS account, active service access and existing EKS/ECR verified.')


def resume_nodes(call=aws):
    cluster = checked_cluster(call)
    ids = set()
    for name in (CLUSTER + '-nodes', CLUSTER + '-storage'):
        group = call('eks', 'describe-nodegroup', '--cluster-name', CLUSTER,
                     '--nodegroup-name', name)['nodegroup']
        groups = {item['name'] for item in group['resources']['autoScalingGroups']}
        if not groups:
            raise SafeError('An existing EKS node group has no backing Auto Scaling group.')
        backing = call('autoscaling', 'describe-auto-scaling-groups',
                       '--auto-scaling-group-names', *sorted(groups))['AutoScalingGroups']
        if {item['AutoScalingGroupName'] for item in backing} != groups:
            raise SafeError('Could not identify every expected EKS Auto Scaling group.')
        members = {instance['InstanceId'] for item in backing for instance in item['Instances']}
        if not members:
            raise SafeError('An EKS node group has no existing EC2 instances; recover its capacity before releasing.')
        ids.update(members)
    response = call('ec2', 'describe-instances', '--instance-ids', *sorted(ids))
    machines = [item for reservation in response['Reservations'] for item in reservation['Instances']]
    if ({item['InstanceId'] for item in machines} != ids
            or any(item['VpcId'] != cluster['resourcesVpcConfig']['vpcId'] for item in machines)):
        raise SafeError('EC2 instances do not match the selected EKS deployment; no start request was sent.')
    if any(item['State']['Name'] not in ('running', 'pending', 'stopped') for item in machines):
        raise SafeError('EKS instances are stopping or terminating; wait for the Lab and retry.')
    stopped = sorted(item['InstanceId'] for item in machines if item['State']['Name'] == 'stopped')
    if stopped:
        call('ec2', 'start-instances', '--instance-ids', *stopped)
    call('ec2', 'wait', 'instance-running', '--instance-ids', *sorted(ids), timeout=660)
    print('PASS: existing EKS instances running; no Vault connection is required.')


def deploy_existing(registry, image_tag, revision, call=aws):
    if registry != REGISTRY or not re.fullmatch(r'[0-9a-f]{40}', image_tag) or not re.fullmatch(r'[0-9]+\.[0-9]+', revision):
        raise SafeError('Use the target ECR registry, a full release SHA and a numeric run/attempt revision.')
    checked_cluster(call)
    raw = delivery.kubectl('get', 'secret', 'boutique-integration', '--ignore-not-found', '-o', 'json')
    if not raw.strip():
        raise SafeError('The original boutique-integration Secret is missing. Restore application custody before releasing; do not regenerate database passwords.')
    values = validate_values(delivery.prior_values(json.loads(raw)))
    delivery.deploy(values, registry, image_tag, revision)
    print('PASS: direct AWS deployment verified with the existing application credentials.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight')
    commands.add_parser('resume-nodes')
    deploy = commands.add_parser('deploy')
    deploy.add_argument('--registry', required=True)
    deploy.add_argument('--image-tag', required=True)
    deploy.add_argument('--revision', required=True)
    args = parser.parse_args()
    def interrupted(*_):
        raise SafeError('Deployment interrupted.')
    signal.signal(signal.SIGTERM, interrupted)
    if args.command == 'preflight':
        preflight()
    elif args.command == 'resume-nodes':
        resume_nodes()
    else:
        deploy_existing(args.registry, args.image_tag, args.revision)


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        sys.exit('Direct AWS release stopped: ' + str(exc))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt):
        sys.exit('Direct AWS release stopped; check service access and application state. Private values were not logged.')
