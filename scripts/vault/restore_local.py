"""One-time private payload preparation and restore of the explicitly accepted local database."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from common import KEYS, SafeError, validate_values
from deploy import NAMESPACE, RELEASE, kubectl, run, secret_manifest
from local_env import verify_admin

DIRECTORY = Path('.vault-lab/bootstrap')


def payload():
    data = json.loads((DIRECTORY / 'local-payload.json').read_text())
    validate_values(data['values'])
    if data.get('repo') != 'Hacket00GTFO/Boutique-Eq4-DevOPS' or data.get('version') != 1:
        raise SafeError('The local recovery payload belongs to another project or format.')
    dump = base64.b64decode(data['database_dump_b64'], validate=True)
    if not dump.startswith(b'PGDMP') or hashlib.sha256(dump).hexdigest() != data['dump_sha256']:
        raise SafeError('The accepted local database backup failed its integrity check.')
    return data, dump


def prepare():
    os.umask(0o077)
    DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = json.loads(os.environ['LOCAL_RECOVERY_PAYLOAD'])
    with os.fdopen(os.open(DIRECTORY / 'local-payload.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(data, stream)
    data, _ = payload()
    server = {key: data[key] for key in ('values', 'pgp_public_keys', 'repo', 'repository_id', 'owner_id')}
    with os.fdopen(os.open(DIRECTORY / 'vault-payload.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(server, stream)
    print('PASS: one-time private payload validated; no environment values or database rows logged.')


def sql(query):
    return kubectl('exec', '-i', 'postgres-0', '--', 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                   '-U', 'postgres', '-d', 'boutique', '-At', payload=query, timeout=90).strip()


def verify_database(expected, values):
    tables = json.loads(sql("SELECT json_agg(schemaname || '.' || tablename) FROM pg_tables "
                            "WHERE schemaname IN ('public','boutique')"))
    if set(tables or []) != set(expected):
        raise SafeError('Restored PostgreSQL tables differ from the accepted local backup.')
    for table, count in expected.items():
        if not re.fullmatch(r'(public|boutique)\.[a-z_]+', table):
            raise SafeError('The recovery record contains an invalid table identifier.')
        if int(sql('SELECT count(*) FROM ' + table)) != count:
            raise SafeError('Restored row counts differ from the accepted local backup.')
    users = json.loads(sql('SELECT json_agg(row_to_json(a)) FROM '
                           '(SELECT email,password_hash,role,active FROM boutique.admin_users) a'))
    verify_admin(values, users or [])


def restore():
    import yaml
    data, dump = payload()
    values = validate_values({key: os.environ.get(key) for key in KEYS})
    if values != data['values']:
        raise SafeError('Vault did not return the exact accepted local application/admin settings.')
    account = run(['aws', 'sts', 'get-caller-identity', '--query', 'Account', '--output', 'text']).strip()
    if account != '716142453772':
        raise SafeError('Restoration requires the original Learner Lab account.')
    run(['kubectl', 'apply', '-f', '-'], payload=json.dumps({
        'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}}))
    previous = kubectl('get', 'secret', 'boutique-integration', '--ignore-not-found', '-o', 'json')
    if previous:
        from deploy import validate_rotation
        validate_rotation(values, json.loads(previous))
    elif json.loads(kubectl('get', 'pvc', '-o', 'json'))['items']:
        raise SafeError('Existing persistent data has no matching credentials; automatic restoration stopped.')
    kubectl('apply', '--server-side', '--field-manager=boutique-vault-cd', '-f', '-',
            payload=json.dumps(secret_manifest(values)))
    rendered = run(['helm', 'template', RELEASE, './helm-chart', '-n', NAMESPACE,
                    '-f', 'helm-chart/values-aws.yaml', '-f', 'helm-chart/values-postgres.yaml',
                    '-f', 'helm-chart/values-vault-lab.yaml', '--show-only', 'templates/postgres-integration.yaml'])
    resources = [item for item in yaml.safe_load_all(rendered) if item and
                 item.get('kind') in ('Service', 'StatefulSet') and item['metadata']['name'] == 'postgres']
    if sorted(item['kind'] for item in resources) != ['Service', 'StatefulSet']:
        raise SafeError('The chart did not produce the expected private PostgreSQL resources.')
    for resource in resources:
        metadata = resource['metadata']
        metadata.setdefault('labels', {})['app.kubernetes.io/managed-by'] = 'Helm'
        metadata.setdefault('annotations', {}).update({
            'meta.helm.sh/release-name': RELEASE, 'meta.helm.sh/release-namespace': NAMESPACE})
    kubectl('apply', '-f', '-', payload=yaml.safe_dump_all(resources))
    kubectl('rollout', 'status', 'statefulset/postgres', '--timeout=600s', timeout=620)
    existing_tables = int(sql("SELECT count(*) FROM pg_tables WHERE schemaname IN ('boutique','public')"))
    if existing_tables:
        marker = kubectl('get', 'configmap', 'local-recovery-verified', '--ignore-not-found', '-o', 'json')
        if not marker or json.loads(marker).get('data', {}).get('dump_sha256') != data['dump_sha256']:
            raise SafeError('PostgreSQL already contains unverified data; no tables were overwritten.')
    else:
        result = subprocess.run(['kubectl', '-n', NAMESPACE, 'exec', '-i', 'postgres-0', '--', 'pg_restore',
                                 '-U', 'postgres', '-d', 'boutique', '--single-transaction', '--exit-on-error',
                                 '--no-owner', '--no-acl'], input=dump, capture_output=True, timeout=300, check=False)
        if result.returncode:
            raise SafeError('The PostgreSQL restore failed atomically; private database output suppressed.')
    verify_database(data['table_counts'], values)
    if sql("SELECT count(*) FROM pg_roles WHERE rolname='boutique_app'") == '0':
        password = values['APP_DB_PASSWORD'].replace("'", "''")
        sql("SET standard_conforming_strings=on; CREATE ROLE boutique_app LOGIN PASSWORD '" + password + "';")
    sql('GRANT USAGE ON SCHEMA boutique TO boutique_app; '
        'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA boutique TO boutique_app; '
        'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA boutique TO boutique_app;')
    marker = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'local-recovery-verified', 'namespace': NAMESPACE},
              'data': {'dump_sha256': data['dump_sha256'], 'admin_env_verified': 'true',
                       'source': 'explicitly accepted local backup; old cloud orders unavailable'}}
    kubectl('apply', '-f', '-', payload=json.dumps(marker))
    record = {'restoration_verified': True, 'admin_env_verified': True, 'dump_sha256': data['dump_sha256'],
              'table_counts': data['table_counts'], 'source': 'accepted local backup'}
    (DIRECTORY / 'database-verified.json').write_text(json.dumps(record, indent=2))
    print('PASS: accepted local PostgreSQL backup restored; row counts and original .env administrator verified.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'restore'])
    try:
        {'prepare': prepare, 'restore': restore}[parser.parse_args().command]()
    except SafeError as error:
        sys.exit('Local recovery stopped: ' + str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Local recovery stopped; no private settings or database output logged.')
