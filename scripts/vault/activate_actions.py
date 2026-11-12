"""Activate CD only from a successful main bootstrap and preserved encrypted custody."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from common import SafeError, private_json
from recovery_evidence import ACCOUNT, REPO

AWS_KEYS = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')
ENVIRONMENT = 'vault-lab'
ARTIFACT = 'local-bootstrap-verification'


def validate_evidence(repo, repository, run, jobs, variables, record, encrypted):
    if (repo != REPO or not repository.get('permissions', {}).get('admin')
            or run.get('repository', {}).get('full_name') != repo
            or run.get('head_branch') != 'main' or run.get('event') != 'workflow_dispatch'
            or run.get('path') != '.github/workflows/ci-docker-ecr.yaml'
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'):
        raise SafeError('Activation requires a successful main bootstrap in the expected repository and admin access.')
    bootstrap = [job for job in jobs if job.get('name') == 'initialize-local-data']
    if len(bootstrap) != 1 or bootstrap[0].get('conclusion') != 'success':
        raise SafeError('The selected run did not complete the one-time bootstrap job.')
    required = ('kms_restart_verified', 'tls_verified', 'oidc_configured', 'root_token_revoked',
                'oidc_login_verified', 'application_values_verified', 'restoration_verified', 'admin_env_verified')
    expected = {'repo': repo, 'repository_id': str(repository['id']), 'owner_id': str(repository['owner']['id']),
                'account': ACCOUNT, 'sha': run['head_sha'], 'run_id': str(run['id']),
                'run_attempt': str(run['run_attempt']), 'instance_id': variables.get('VAULT_INSTANCE_ID'),
                'kms_key_arn': variables.get('VAULT_KMS_KEY_ARN'), 'ssm_bucket': variables.get('VAULT_SSM_BUCKET')}
    if (record.get('version') != 1 or any(record.get(k) != v or not v for k, v in expected.items())
            or any(record.get(field) is not True for field in required)
            or not re.fullmatch(r'[0-9a-f]{64}', record.get('dump_sha256', ''))
            or not all(record.get(k) for k in ('pvc_uid', 'volume_name', 'volume_handle', 'table_counts'))
            or not encrypted or hashlib.sha256(encrypted).hexdigest() != record.get('custody_sha256')):
        raise SafeError('Bootstrap evidence or encrypted custody does not match this run and current infrastructure.')


def preserve_custody(directory, record, encrypted):
    if os.name != 'posix':
        raise SafeError('Preserve bootstrap custody from Linux/WSL with private filesystem permissions.')
    directory = Path(directory).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if directory == repo or repo in directory.parents:
        raise SafeError('The permanent custody directory must be outside the repository.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise SafeError('The permanent custody directory must be owned by the operator with mode 0700.')
    destination = directory / ('actions-' + record['run_id'] + '-' + record['run_attempt'])
    if destination.is_symlink():
        raise SafeError('Custody destination cannot be a symlink.')
    destination.mkdir(mode=0o700, exist_ok=True)
    if destination.stat().st_uid != os.getuid() or destination.stat().st_mode & 0o077:
        raise SafeError('The run custody directory must have mode 0700.')
    target = destination / 'vault-custody.tar.gpg'
    if target.exists():
        if target.is_symlink() or target.read_bytes() != encrypted:
            raise SafeError('Existing custody differs; it was not overwritten.')
    else:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(encrypted)
    receipt = destination / 'bootstrap-verified.json'
    if receipt.exists():
        if receipt.is_symlink() or json.loads(receipt.read_text()) != record:
            raise SafeError('Existing bootstrap receipt differs; it was not overwritten.')
    else:
        private_json(receipt, record)
    if hashlib.sha256(target.read_bytes()).hexdigest() != record['custody_sha256']:
        raise SafeError('The durable custody copy failed its integrity check.')
    return destination


def activate(repo, run_id, custody_dir, api, command, set_value, prepare_environment):
    repository = api(repo)
    run = api(repo, f'actions/runs/{run_id}')
    attempt = run.get('run_attempt', 0)
    jobs = api(repo, f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100').get('jobs', [])
    environment_path = f'environments/{ENVIRONMENT}'
    environment = api(repo, environment_path)
    variables = {v['name']: v['value'] for v in api(repo, environment_path + '/variables?per_page=100').get('variables', [])}
    repository_secrets = {s['name'] for s in api(repo, 'actions/secrets?per_page=100').get('secrets', [])}
    if not set(AWS_KEYS).issubset(repository_secrets):
        raise SafeError('All three AWS repository secrets must exist before removing environment overrides.')
    with tempfile.TemporaryDirectory(prefix='boutique-activation-') as directory:
        command(['gh', 'run', 'download', str(run_id), '--repo', repo, '--name', ARTIFACT, '--dir', directory])
        record = json.loads((Path(directory) / 'bootstrap-verified.json').read_text())
        encrypted = (Path(directory) / 'vault-custody.tar.gpg').read_bytes()
        validate_evidence(repo, repository, run, jobs, variables, record, encrypted)
        custody = preserve_custody(custody_dir, record, encrypted)
    # All evidence and durable custody checks precede any GitHub mutation.
    prepare_environment(repo, environment)
    set_value(repo, 'variable', 'VAULT_AUTOMATION_READY', 'false', environment=True)
    for prefix in ('actions', environment_path):
        names = {s['name'] for s in api(repo, prefix + '/secrets?per_page=100').get('secrets', [])}
        obsolete = {'VAULT_CI_TOKEN', 'LOCAL_RECOVERY_PAYLOAD'}
        if prefix == environment_path:
            obsolete.update(AWS_KEYS)
        for name in sorted(names & obsolete):
            api(repo, prefix + '/secrets/' + name, 'DELETE')
    if 'VAULT_CA_CERT_B64' in variables:
        api(repo, environment_path + '/variables/VAULT_CA_CERT_B64', 'DELETE')
    set_value(repo, 'variable', 'AWS_ACCOUNT_ID', ACCOUNT)
    set_value(repo, 'variable', 'DATA_RECOVERY_VERIFIED', 'true', environment=True)
    set_value(repo, 'variable', 'VAULT_AUTOMATION_READY', 'true', environment=True)
    installed = {v['name']: v['value'] for v in api(repo, environment_path + '/variables?per_page=100').get('variables', [])}
    if any(installed.get(name) != 'true' for name in ('DATA_RECOVERY_VERIFIED', 'VAULT_AUTOMATION_READY')):
        raise SafeError('Activation read-back failed; verify the environment before releasing.')
    print('PASS: verified recovery and real GitHub OIDC activated; encrypted custody preserved at ' + str(custody))
