import json
import os
import pathlib
import subprocess
import sys

CONTROL = pathlib.Path(__file__).resolve().parents[1]
WORK = pathlib.Path(os.environ['GITHUB_WORKSPACE']) / 'candidate'
OUT = pathlib.Path(os.environ['RUNNER_TEMP']) / 'repair-result'
OUT.mkdir(exist_ok=True)
NUMBER = os.environ['PR_NUMBER']
EXPECTED = os.environ['EXPECTED_HEAD']
HEAD_BRANCH = os.environ['HEAD_BRANCH']
FORK = 'AaronZ345/qwen-code'
UPSTREAM = 'QwenLM/qwen-code'
PATCH = CONTROL / '_maintenance' / 'patches' / (NUMBER + '.patch')

SPECS = {
    '10251': {
        'package': 'packages/cli',
        'tests': ['src/commands/review/publish-assets.test.ts', 'src/commands/review/lib/platform/github.test.ts'],
        'red_file': 'packages/cli/src/commands/review/publish-assets.test.ts',
        'red_pattern': 'folds --reviewed-repo',
        'red_evidence': '--repo',
        'typecheck': 'packages/cli',
        'message': 'fix(review): resolve asset targets with valid shared gh arguments',
        'files': ['packages/cli/src/commands/review/publish-assets.ts', 'packages/cli/src/commands/review/publish-assets.test.ts', 'packages/cli/src/commands/review/lib/platform/github.ts', 'packages/cli/src/commands/review/lib/platform/github.test.ts', 'packages/cli/vitest.config.ts'],
    },
    '10280': {
        'package': 'packages/core',
        'tests': ['src/core/coreToolScheduler.test.ts'],
        'red_file': 'packages/core/src/core/coreToolScheduler.test.ts',
        'red_pattern': 'adds the stop directive after',
        'red_evidence': 'Stop and await further instructions',
        'typecheck': 'packages/core',
        'message': 'fix(core): preserve cancellation reasons without inventing user intent',
        'files': ['packages/core/src/core/coreToolScheduler.ts', 'packages/core/src/core/coreToolScheduler.test.ts', 'packages/cli/vitest.config.ts'],
    },
}
extra = CONTROL / '_maintenance' / 'specs.json'
if extra.exists():
    SPECS.update(json.loads(extra.read_text()))
SPEC = SPECS[NUMBER]


def capture(args, cwd=CONTROL):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def api(path, method='GET', **fields):
    args = ['gh', 'api', '--method', method, path]
    for key, value in fields.items():
        args += ['-f', f'{key}={value}']
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f'GitHub operation failed: {method} {path}: {result.stdout}')
    return json.loads(result.stdout) if result.stdout.strip() else None


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2))


def run(args, name, cwd=WORK, check=True):
    print('+ ' + ' '.join(args), flush=True)
    with (OUT / (name + '.log')).open('w') as log:
        proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            print(line, end='', flush=True)
        code = proc.wait()
    if check and code:
        raise RuntimeError(f'{name} failed with exit code {code}')
    return code


def verify_pr():
    pr = api(f'repos/{UPSTREAM}/pulls/{NUMBER}')
    assert pr['state'] == 'open', pr['state']
    assert pr['head']['sha'] == EXPECTED, 'Original PR changed; refusing to overwrite it'
    assert pr['head']['ref'] == HEAD_BRANCH
    assert pr['head']['repo']['full_name'] == FORK
    return pr


def install():
    run(['corepack', 'enable'], 'corepack')
    run(['corepack', 'pnpm', 'install', '--frozen-lockfile'], 'install')


def red():
    test = str(pathlib.Path(SPEC['red_file']).relative_to(SPEC['package']))
    code = run(['npx', '--no-install', 'vitest', 'run', test, '-t', SPEC['red_pattern'], '--maxWorkers', '2', '--reporter=json', '--outputFile=' + str(OUT/'red.json')], 'red', WORK/SPEC['package'], False)
    assert code != 0, 'The expected defect did not reproduce; do not claim a fix'
    result = json.loads((OUT/'red.json').read_text())
    failures = [a for f in result['testResults'] for a in f['assertionResults'] if a['status'] == 'failed']
    assert failures and all(SPEC['red_pattern'] in a['fullName'] for a in failures), failures
    assert any(SPEC['red_evidence'] in '\n'.join(a['failureMessages']) for a in failures), failures
    save('reproduced.json', {'expected_failures': len(failures), 'pattern': SPEC['red_pattern']})


def fix():
    run(['git', 'apply', '--exclude=' + SPEC['red_file'], str(PATCH)], 'apply-fix')
    run(['npx', '--no-install', 'prettier', '--write', *SPEC['files']], 'format')
    run(['git', 'diff', '--check'], 'diff-check')
    (OUT/'fix.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=WORK))


def build():
    command = ['npm', 'run', 'build']
    if SPEC.get('build_workspace'):
        command += ['--workspace', SPEC['build_workspace']]
    run(command, 'build')
    if SPEC.get('typecheck'):
        run(['npm', 'run', 'typecheck', '--workspace', SPEC['typecheck']], 'typecheck')
    if NUMBER == '10251':
        run(['npm', 'run', 'bundle'], 'bundle')


def green():
    suites = SPEC.get('test_suites', [{'package': SPEC['package'], 'tests': SPEC['tests']}])
    for index, suite in enumerate(suites):
        name = 'green' if index == 0 else f'green-{index}'
        run(['npx', '--no-install', 'vitest', 'run', *suite['tests'], '--maxWorkers', '2', '--reporter=default', '--reporter=json', '--outputFile.json=' + str(OUT/(name+'.json'))], name, WORK/suite['package'])
    lint_files = [p for p in SPEC['files'] if p.endswith(('.ts', '.tsx', '.js', '.jsx', '.mjs'))]
    run(['npx', '--no-install', 'eslint', '--max-warnings', '0', *lint_files], 'lint')
    if NUMBER == '10251':
        run(['node', 'dist/cli.js', 'review', 'publish-assets', '--help'], 'cli-help')
        view = json.loads(capture(['gh', 'repo', 'view', FORK, '--json', 'owner,name,url,parent'], WORK))
        assert view['owner']['login'] and view['name'] and view['url']
        save('real-gh-probe.json', view)


if __name__ == '__main__':
    phases = {'install': install, 'red': red, 'fix': fix, 'build': build, 'green': green}
    phases[sys.argv[1]]()
