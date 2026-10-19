"""Merge development into explicit team branches; default to a local preview."""
import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile


class GitError(RuntimeError):
    pass


def git(*args, cwd=None, check=True):
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120)
    if check and result.returncode:
        # No remote URLs, credential helpers, or arbitrary Git output in reports.
        raise GitError('Git no pudo completar la operación; revisa conectividad y permisos.')
    return result


@dataclass
class Result:
    branch: str
    status: str
    note: str
    failed: bool = False


def sync_target(repo, branch, source, push):
    ref = 'refs/remotes/origin/' + branch
    target = git('rev-parse', '--verify', ref + '^{commit}', cwd=repo, check=False)
    if target.returncode:
        return Result(branch, 'Failed', 'La rama destino no existe en origin.', True)
    target_sha = target.stdout.strip()
    if git('merge-base', '--is-ancestor', source, target_sha, cwd=repo, check=False).returncode == 0:
        return Result(branch, 'Already up to date', 'Ya contiene development.')
    if git('merge-base', source, target_sha, cwd=repo, check=False).returncode:
        return Result(branch, 'Failed', 'Las ramas no comparten historial.', True)

    # The detached worktree preserves the caller's checkout and destination history.
    with tempfile.TemporaryDirectory(prefix='boutique-branch-sync-') as directory:
        checkout = Path(directory) / 'checkout'
        git('worktree', 'add', '--detach', str(checkout), target_sha, cwd=repo)
        try:
            merge = git('merge', '--no-edit', '--no-ff', source, cwd=checkout, check=False)
            if merge.returncode:
                conflicts = git('diff', '--name-only', '--diff-filter=U', cwd=checkout).stdout.strip()
                git('merge', '--abort', cwd=checkout, check=False)
                return Result(branch, 'Conflict' if conflicts else 'Failed',
                              'Se requiere resolver el merge manualmente.' if conflicts else 'No se pudo crear el merge.', True)
            if not push:
                return Result(branch, 'Preview', 'Merge válido; no se modificó origin.')
            # Normal push: a concurrent update rejects instead of losing commits.
            result = git('push', 'origin', 'HEAD:refs/heads/' + branch, cwd=checkout, check=False)
            if result.returncode:
                return Result(branch, 'Push failed', 'Revisa permisos, protección de rama o cambios concurrentes.', True)
            return Result(branch, 'Synced', 'development fusionado conservando los commits propios.')
        finally:
            git('worktree', 'remove', '--force', str(checkout), cwd=repo)


def write_summary(source, results, push):
    lines = ['## Sync development into team branches', '',
             'Source: `development` @ `' + source[:12] + '`',
             'Mode: ' + ('push' if push else 'preview (sin cambios remotos)'), '',
             '| Branch | Status | Notes |', '| --- | --- | --- |']
    for result in results:
        branch = result.branch.replace('|', r'\|').replace('`', '')
        lines.append(f'| `{branch}` | {result.status} | {result.note} |')
        print(f'{result.branch}: {result.status}. {result.note}')
    failed = sum(result.failed for result in results)
    lines.extend(['', f'Destinos: {len(results)}. Fallos: {failed}.'])
    if path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', action='append', required=True,
                        help='Explicit destination; repeat for each team branch')
    parser.add_argument('--push', action='store_true', help='Push successful merges to origin')
    args = parser.parse_args()
    targets = list(dict.fromkeys(args.target))
    for branch in targets:
        if (branch in ('main', 'development', 'gh-pages', 'HEAD') or branch.startswith(('release/', 'sync/'))
                or git('check-ref-format', '--branch', branch, check=False).returncode):
            parser.error('Destino protegido o nombre de rama inválido.')
    repo = git('rev-parse', '--show-toplevel').stdout.strip()
    # Fetch all branch refs even if checkout used a narrow fetch configuration.
    git('fetch', '--prune', 'origin', '+refs/heads/*:refs/remotes/origin/*', cwd=repo)
    source = git('rev-parse', '--verify', 'refs/remotes/origin/development^{commit}', cwd=repo).stdout.strip()
    results = []
    for branch in targets:
        try:
            results.append(sync_target(repo, branch, source, args.push))
        except (GitError, subprocess.TimeoutExpired, OSError):
            results.append(Result(branch, 'Failed', 'Error de Git o tiempo de espera agotado; revisa el destino.', True))
    write_summary(source, results, args.push)
    return int(any(result.failed for result in results))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (GitError, subprocess.TimeoutExpired, OSError):
        print('No se pudo preparar la sincronización. Comprueba origin, development y los permisos de Git.')
        raise SystemExit(1)
