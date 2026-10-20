"""Transactional lab Secret delivery and Helm deployment; no secret files or CLI arguments."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from common import DB_KEYS, KEYS, SafeError, validate_values

NAMESPACE = 'boutique-vault-lab'
RELEASE = 'boutique-vault-lab'
STORAGE_CLASS = 'boutique-ebs-gp3'
CONSUMERS = ('catalogservice', 'frontend', 'productcatalogservice', 'checkoutservice')


def run(args, payload=None, timeout=90):
    result = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise SafeError('External command failed: ' + args[0] + '; sensitive output suppressed.')
    return result.stdout


def kubectl(*args, payload=None, timeout=90):
    return run(['kubectl', '--namespace', NAMESPACE, *args], payload, timeout)


def secret_manifest(values):
    return {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
            'metadata': {'name': 'boutique-integration', 'namespace': NAMESPACE},
            'data': {k: base64.b64encode(values[k].encode()).decode() for k in KEYS}}


def prior_values(previous):
    try:
        return {k: base64.b64decode(previous['data'][k], validate=True).decode() for k in KEYS}
    except (KeyError, ValueError, UnicodeError):
        raise SafeError('Existing integration Secret is incomplete; repair it before deploying.') from None


def validate_rotation(values, previous):
    if previous:
        old = prior_values(previous)
        if any(values[k] != old[k] for k in (*DB_KEYS, 'ADMIN_EMAIL', 'ADMIN_PASSWORD')):
            raise SafeError('Database/bootstrap credentials changed. Use the coordinated rotation runbook, not automatic CD.')


def restore_manifest(previous):
    # Do not reapply resourceVersion, managedFields or stale server-side identity.
    return {'apiVersion': 'v1', 'kind': 'Secret', 'type': previous.get('type', 'Opaque'),
            'metadata': {'name': 'boutique-integration', 'namespace': NAMESPACE},
            'data': previous['data']}


def smoke(values):
    # Reuse the established real HTTP/gRPC/SQL integration suite INSIDE the lab pod.
    # Credentials and script travel on stdin, never kubectl arguments or disk.
    script = (Path(__file__).resolve().parents[1] / 'test-storefront-integration.py').read_text(encoding='utf-8')
    loader = ('import json,os,sys; p=json.load(sys.stdin); os.environ.update(p["env"]); '
              'exec(compile(p["script"],"vault-lab-smoke","exec"))')
    smoke_credentials = {k: values[k] for k in ('ADMIN_EMAIL', 'ADMIN_PASSWORD', 'ADMIN_API_TOKEN')}
    # Kubernetes exposes the frontend Service on 80; Compose exposes it on 8080.
    smoke_credentials['STOREFRONT_TEST_URL'] = 'http://frontend:80'
    output = kubectl('exec', '-i', 'deployment/catalogservice', '--', 'python', '-c', loader,
                     payload=json.dumps({'env': smoke_credentials, 'script': script}), timeout=240)
    try:
        summary = json.loads(output.strip().splitlines()[-1])
        passed = int(summary['passed'])
        if passed < 17:
            raise ValueError()
    except (ValueError, KeyError, IndexError):
        raise SafeError('Functional smoke did not return a complete success summary.') from None
    print(f'PASS functional HTTP/gRPC/PostgreSQL smoke: {passed} checks.')


def deploy(values, registry, image_tag, revision):
    values = validate_values(values)  # Always before ANY Kubernetes write.
    if not re.fullmatch(r'[0-9a-f]{40}', image_tag):
        raise SafeError('A built full commit SHA is required.')
    if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com', registry):
        raise SafeError('An ECR registry in the configured lab region is required.')
    if not re.fullmatch(r'[0-9]+\.[0-9]+', revision):
        raise SafeError('Revision must be the GitHub run ID and attempt.')
    run(['kubectl', 'get', 'namespace', NAMESPACE])
    # Readiness of persistent storage is a prerequisite, never silently use ephemeral DB data.
    classes = json.loads(run(['kubectl', 'get', 'storageclass', '-o', 'json']))
    storage = next((c for c in classes.get('items', [])
                    if c.get('metadata', {}).get('name') == STORAGE_CLASS), None)
    if not storage or storage.get('provisioner') != 'ebs.csi.aws.com' or storage.get('volumeBindingMode') != 'WaitForFirstConsumer':
        raise SafeError('The lab needs boutique-ebs-gp3 with EBS CSI and WaitForFirstConsumer before deployment.')
    run(['kubectl', 'get', 'csidriver', 'ebs.csi.aws.com', '-o', 'json'])
    previous_raw = kubectl('get', 'secret', 'boutique-integration', '--ignore-not-found', '-o', 'json')
    previous = json.loads(previous_raw) if previous_raw.strip() else None
    validate_rotation(values, previous)
    pvcs = json.loads(kubectl('get', 'pvc', '-o', 'json'))['items']
    if pvcs and not previous:
        raise SafeError('Persistent data exists without its original Secret. Restore custody before deploying.')
    releases = json.loads(run(['helm', 'list', '-n', NAMESPACE, '--all', '-o', 'json']))
    current = next((r for r in releases if r['name'] == RELEASE), None)
    if current and current['status'] != 'deployed':
        raise SafeError('Existing release is not deployed; recover its Helm state before retrying.')
    helm_args = ['helm', 'upgrade', '--install', RELEASE, './helm-chart', '-n', NAMESPACE,
                 '-f', 'helm-chart/values-aws.yaml', '-f', 'helm-chart/values-postgres.yaml',
                 '-f', 'helm-chart/values-vault-lab.yaml',
                 '--set-string', 'images.repository=' + registry + '/boutique-eq4',
                 '--set-string', 'images.tag=' + image_tag,
                 '--set-string', 'postgresIntegration.database.storageClassName=' + STORAGE_CLASS,
                 '--set-string', 'postgresIntegration.rolloutRevision=' + revision,
                 '--atomic', '--wait', '--wait-for-jobs', '--timeout', '10m', '--history-max', '5']
    changed = False
    try:
        # Mark BEFORE the request: a server may commit a write even if the response is lost.
        changed = True
        kubectl('apply', '--server-side', '--field-manager=boutique-vault-cd', '-f', '-',
                payload=json.dumps(secret_manifest(values)))
        run(helm_args, timeout=1300)  # Atomic upgrade + rollback can each need ten minutes.
        smoke(values)
        print('PASS Vault lab deployment and migration completed.')
    except BaseException:
        if changed:
            try:
                if not current:
                    # A successful first install can still fail functional smoke.
                    # Remove its workloads, preserving StatefulSet PVCs and their credentials.
                    run(['helm', 'uninstall', RELEASE, '-n', NAMESPACE, '--ignore-not-found',
                         '--wait', '--timeout', '3m'], timeout=190)
                if previous:
                    kubectl('apply', '--server-side', '--field-manager=boutique-vault-cd', '-f', '-',
                            payload=json.dumps(restore_manifest(previous)))
                    if current:
                        run(['helm', 'rollback', RELEASE, str(current['revision']), '-n', NAMESPACE,
                             '--wait', '--wait-for-jobs', '--timeout', '10m'], timeout=650)
                        # Atomic rollback may already have started pods with the failed Secret.
                        for app in CONSUMERS:
                            kubectl('rollout', 'restart', 'deployment/' + app)
                        for app in CONSUMERS:
                            kubectl('rollout', 'status', 'deployment/' + app, '--timeout=180s', timeout=190)
                        smoke(prior_values(previous))
                        print('Previous Secret restored; existing release recovery verified.')
                    else:
                        print('Previous Secret restored; failed initial release removed.')
                elif not json.loads(kubectl('get', 'pvc', '-o', 'json'))['items']:
                    kubectl('delete', 'secret', 'boutique-integration', '--ignore-not-found')
                    print('Failed initial install cleaned; no persistent database was created.')
                else:
                    print('Initial install failed; Secret retained to preserve access to the new database PVC.')
            except BaseException:
                print('RECOVERY REQUIRED: rollback could not be verified; preserve the Secret and PVC.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--image-tag', required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    def interrupted(*_):
        raise SafeError('Deployment interrupted.')
    signal.signal(signal.SIGTERM, interrupted)
    deploy({k: os.environ.get(k) for k in KEYS}, args.registry, args.image_tag, args.revision)


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        print('Lab deploy failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt):
        print('Lab deploy failed; sensitive command output was suppressed. Check prerequisites and recovery status.', file=sys.stderr)
        sys.exit(1)
