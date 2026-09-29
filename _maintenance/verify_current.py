import json
import os
import pathlib
import subprocess
import sys

import verify as v

BRANCH = f'repair/candidate-{v.NUMBER}-20260929'


def prepare():
    v.save('pr-before.json', v.verify_pr())
    upstream = v.api(f'repos/{v.UPSTREAM}/git/ref/heads/main')['object']['sha']
    remote = v.api(f'repos/{v.FORK}/git/ref/heads/{BRANCH}')['object']['sha']
    assert remote == v.EXPECTED, 'The validation branch changed'
    v.save('integration.json', {
        'sha': remote, 'upstream_observed': upstream,
        'upstream_merged': False, 'branch': BRANCH,
    })
    v.run(['git', 'fetch', '--no-tags', '--depth=1', 'origin', BRANCH], 'fetch-current', v.CONTROL)
    assert v.capture(['git', 'rev-parse', 'FETCH_HEAD']) == v.EXPECTED
    v.run(['git', 'worktree', 'add', '--detach', str(v.WORK), v.EXPECTED], 'checkout-current', v.CONTROL)
    assert v.capture(['git', 'hash-object', str(v.PATCH)]) == os.environ['PATCH_BLOB']
    v.run(['git', 'apply', '--check', str(v.PATCH)], 'patch-preflight')
    v.run(['git', 'apply', '--include=' + v.SPEC['red_file'], str(v.PATCH)], 'apply-regression')


def publish():
    v.verify_pr()
    remote = v.api(f'repos/{v.FORK}/git/ref/heads/{BRANCH}')['object']['sha']
    assert remote == v.EXPECTED, 'The candidate branch moved; refusing to overwrite'
    changed = set(v.capture(['git', 'diff', '--name-only'], v.WORK).splitlines())
    assert changed and changed <= set(v.SPEC['files']), changed
    v.run(['git', 'diff', '--check'], 'final-diff-check')
    v.run(['git', 'config', 'user.name', 'Yu Zhang'], 'git-name')
    v.run(['git', 'config', 'user.email', '34849476+AaronZ345@users.noreply.github.com'], 'git-email')
    v.run(['git', 'add', *v.SPEC['files']], 'stage')
    v.run(['git', 'commit', '-m', v.SPEC['message']], 'commit')
    sha = v.capture(['git', 'rev-parse', 'HEAD'], v.WORK)
    v.save('verified-files.json', {
        name: {'content': (v.WORK/name).read_text(),
               'sha': v.capture(['git', 'hash-object', name], v.WORK)}
        for name in sorted(changed)
    })
    result = {
        'pr': v.NUMBER, 'original_head': v.EXPECTED,
        'candidate_sha': sha, 'candidate_branch': BRANCH,
        'verified': True, 'original_pr_updated': False,
        'base_tree': v.capture(['git', 'rev-parse', v.EXPECTED + '^{tree}'], v.WORK),
        'candidate_tree': v.capture(['git', 'rev-parse', 'HEAD^{tree}'], v.WORK),
    }
    code = v.run(['git', 'push', 'origin', f'HEAD:refs/heads/{BRANCH}'], 'push-candidate', check=False)
    result['candidate_published'] = code == 0
    v.save('result.json', result)
    if code:
        raise RuntimeError('Validation passed; the runner could not publish. Verified files are retained for the authorized connector.')


if __name__ == '__main__':
    {'prepare': prepare, 'publish': publish}[sys.argv[1]]()
