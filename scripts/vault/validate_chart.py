"""Render real Helm variants and verify Secret rollout/migration contracts."""
import os
import subprocess
import yaml


def render(*overrides):
    output = subprocess.check_output([os.environ.get('HELM_BINARY', 'helm'), 'template',
                                      'boutique-vault-lab', './helm-chart', '-n', 'boutique-vault-lab', *overrides], text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def main():
    subprocess.run([os.environ.get('HELM_BINARY', 'helm'), 'lint', './helm-chart'], check=True)
    base = render()
    assert not any(d['kind'] == 'Job' for d in base), 'Base chart unexpectedly requires PostgreSQL'
    postgres = render('-f', 'helm-chart/values-postgres.yaml')
    assert any(d['kind'] == 'Job' for d in postgres)
    args = ['-f', 'helm-chart/values-aws.yaml', '-f', 'helm-chart/values-postgres.yaml', '-f', 'helm-chart/values-vault-lab.yaml']
    first = render(*args, '--set-string', 'postgresIntegration.rolloutRevision=123.1')
    second = render(*args, '--set-string', 'postgresIntegration.rolloutRevision=123.2')
    jobs = [next(d for d in docs if d['kind'] == 'Job')['metadata']['name'] for docs in (first, second)]
    assert jobs[0] != jobs[1], 'Re-running the same image must create a fresh migration job'
    for docs, revision in ((first, '123.1'), (second, '123.2')):
        for app in ('frontend', 'catalogservice', 'checkoutservice', 'productcatalogservice'):
            deployment = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == app)
            assert deployment['spec']['template']['metadata']['annotations']['boutique.dev/secret-revision'] == revision
        assert any(d['kind'] == 'StatefulSet' and d['metadata']['name'] == 'postgres' for d in docs)
        assert all(d['metadata'].get('namespace') == 'boutique-vault-lab' for d in docs)
        assert not any(d['kind'] == 'Secret' and d['metadata']['name'] == 'boutique-integration' for d in docs), 'Helm must never persist application secret values'
        assert not any(d['kind'] == 'Service' and d['spec'].get('type') == 'LoadBalancer' for d in docs)
    print('PASS base/PostgreSQL/lab renders, isolated namespace, private frontend, new migration job and four consumer rollouts.')


if __name__ == '__main__':
    main()
