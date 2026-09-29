import json
import pathlib
import subprocess
import sys

import verify as v


def rollback():
    paths = set(v.SPEC.get('revert_pr_files', []))
    if not paths:
        return
    v.verify_pr()
    files = []
    page = 1
    while True:
        batch = v.api(f'repos/{v.UPSTREAM}/pulls/{v.NUMBER}/files?per_page=100&page={page}')
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    selected = {f['filename']: f for f in files if f['filename'] in paths}
    assert set(selected) == paths, 'The PR scope changed; refusing a partial rollback'
    text = ''
    for path in sorted(paths):
        file = selected[path]
        assert file['status'] == 'modified' and file.get('patch'), file
        assert not file.get('previous_filename'), file
        text += f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n' + file['patch'] + '\n'
    v.verify_pr()
    patch = v.OUT/'rollback-unrelated.patch'
    patch.write_text(text)
    v.save('rollback-files.json', selected)
    v.run(['git', 'apply', '--reverse', '--check', str(patch)], 'rollback-preflight')
    v.run(['git', 'apply', '--reverse', str(patch)], 'rollback-unrelated')


def fix():
    v.run(['git', 'apply', '--exclude=' + v.SPEC['red_file'], str(v.PATCH)], 'apply-fix')
    for index, name in enumerate(v.SPEC.get('additional_patches', [])):
        path = v.CONTROL/'_maintenance'/'patches'/name
        v.run(['git', 'apply', '--check', str(path)], f'extra-preflight-{index}')
        v.run(['git', 'apply', str(path)], f'apply-extra-{index}')
    files = v.SPEC.get('format_files', v.SPEC['files'])
    v.run(['npx', '--no-install', 'prettier', '--write', *files], 'format')
    v.run(['git', 'diff', '--check'], 'diff-check')
    (v.OUT/'fix.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=v.WORK))


if __name__ == '__main__':
    {'rollback': rollback, 'fix': fix}[sys.argv[1]]()
