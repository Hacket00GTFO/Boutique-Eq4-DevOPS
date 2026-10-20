"""Authorize a main release using the merged development PR associated with its SHA."""
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request


def merged_development(pr, repo, sha):
    return bool(pr.get('merged_at') and pr.get('merge_commit_sha') == sha
                and pr.get('base', {}).get('ref') == 'main'
                and pr.get('head', {}).get('ref') == 'development'
                and (pr.get('head', {}).get('repo') or {}).get('full_name') == repo)


def authorize(event_name, ref, sha, repo, event, lookup):
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or sha == '0' * 40:
        raise ValueError('A full existing commit SHA is required.')
    if ref != 'refs/heads/main':
        return False, 'This branch permits recovery inventory only; release requires main.'
    if event_name == 'workflow_dispatch':
        return True, 'Explicit manual release from main.'
    if event_name != 'push' or event.get('deleted'):
        return False, 'This event does not authorize a release.'
    matches = [pr for pr in lookup(repo, sha) if merged_development(pr, repo, sha)]
    if len(matches) != 1:
        return False, 'The main SHA is not associated with exactly one merged development PR from this repository.'
    return True, 'Merged development PR verified for the exact main SHA.'


def associated_prs(repo, sha):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('A repository identifier is required.')
    token = os.environ.get('GH_TOKEN', '')
    if not token:
        raise ValueError('A read-only GitHub token is required to verify the merged PR.')
    request = urllib.request.Request('https://api.github.com/repos/' + repo + '/commits/' + sha + '/pulls?per_page=100',
                                     headers={'Accept': 'application/vnd.github+json',
                                              'Authorization': 'Bearer ' + token,
                                              'X-GitHub-Api-Version': '2022-11-28'})
    # A push may arrive just before GitHub finishes marking its PR as merged.
    for attempt in range(10):
        with urllib.request.urlopen(request, timeout=15) as response:
            prs = json.load(response)
        if not isinstance(prs, list):
            raise ValueError('GitHub did not return the merged PR metadata.')
        if any(merged_development(pr, repo, sha) for pr in prs) or attempt == 9:
            return prs
        time.sleep(3)


def main():
    sha = os.environ['GITHUB_SHA']
    allowed, reason = authorize(os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'], sha,
                                os.environ['GITHUB_REPOSITORY'],
                                json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()), associated_prs)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('deploy=' + str(allowed).lower() + '\nimage_tag=' + sha + '\n')
    print(reason)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        sys.exit('Release authorization failed; GitHub credentials and raw responses were not logged.')
