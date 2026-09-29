import json
import os
import pathlib
import re
import subprocess

import verify as v

MESSAGE = 'Could not compress chat history because there is not enough room for the input and summary.'
CORE = 'packages/core/src/services/chatCompressionService.ts'


def replace(path, before, after):
    target = v.WORK / path
    text = target.read_text()
    assert text.count(before) == 1, (path, before, text.count(before))
    target.write_text(text.replace(before, after))


def prepare_merge():
    assert v.NUMBER == '9541'
    v.save('pr-before.json', v.verify_pr())
    branch = 'repair/candidate-9541-20260929'
    assert v.api(f'repos/{v.FORK}/git/ref/heads/{branch}')['object']['sha'] == v.EXPECTED
    upstream = v.api(f'repos/{v.UPSTREAM}/git/ref/heads/main')['object']['sha']
    comparison = v.api(f'repos/{v.UPSTREAM}/compare/{upstream}...{v.EXPECTED}')
    base = comparison['merge_base_commit']['sha']
    source = 'https://github.com/' + v.UPSTREAM + '.git'
    for label, ref in [('head', 'refs/pull/9541/head'), ('upstream', upstream), ('base', base)]:
        v.run(['git', 'fetch', '--no-tags', '--depth=1', source, ref], 'fetch-' + label, v.CONTROL)
    result = subprocess.run(['git', 'merge-tree', '--write-tree', '--name-only', '--merge-base=' + base, v.EXPECTED, upstream], cwd=v.CONTROL, text=True, capture_output=True)
    assert result.returncode in (0, 1), result.stderr
    (v.OUT/'merge-output.txt').write_text(result.stdout)
    lines = result.stdout.splitlines()
    tree = lines[0]
    conflicts = []
    if result.returncode:
        for line in lines[1:]:
            if not line:
                break
            conflicts.append(line)
    assert conflicts == [CORE], conflicts
    v.run(['git', 'worktree', 'add', '--detach', str(v.WORK), v.EXPECTED], 'checkout', v.CONTROL)
    v.run(['git', 'read-tree', '--reset', '-u', tree], 'load-three-way-tree')
    target = v.WORK / CORE
    text = target.read_text()
    text, count = re.subn(r'^<<<<<<< [^\n]*\n(.*?)^=======\n.*?^>>>>>>> [^\n]*\n', r'\1', text, flags=re.M | re.S)
    assert count == 1
    target.write_text(text)
    replace(CORE, '''      const resolved = resolveModelId(model);
      if (!resolved) return undefined;
      const models = resolved.authType
        ? config.getAllConfiguredModels([resolved.authType])
        : config.getAllConfiguredModels();
      return models.find((entry) => entry.id === resolved.modelId)
        ?.contextWindowSize;''', '''      const [selector, endpoint] = model.split('\\0');
      const resolved = resolveModelId(selector);
      if (!resolved) return undefined;
      const models = resolved.authType
        ? config.getAllConfiguredModels([resolved.authType])
        : config.getAllConfiguredModels();
      const sameId = models.filter((entry) => entry.id === resolved.modelId);
      const entry =
        (endpoint
          ? sameId.find((candidate) => candidate.registryBaseUrl === endpoint)
          : undefined) ?? sameId[0];
      return entry?.contextWindowSize;''')
    replace(CORE, 'const [compactionSelector, compactionEndpoint] =', 'const [compactionSelector] =')
    assert not re.search(r'^(<<<<<<<|=======|>>>>>>>)', target.read_text(), re.M)
    v.run(['git', 'diff', '--check'], 'merge-diff-check')
    v.run(['git', 'add', CORE], 'stage-conflict-resolution')
    v.run(['git', 'config', 'user.name', 'Yu Zhang'], 'merge-author-name')
    v.run(['git', 'config', 'user.email', '34849476+AaronZ345@users.noreply.github.com'], 'merge-author-email')
    merged_tree = v.capture(['git', 'write-tree'], v.WORK)
    commit = v.capture(['git', 'commit-tree', merged_tree, '-p', v.EXPECTED, '-p', upstream, '-m', 'Merge current upstream and preserve endpoint-aware compression admission'], v.WORK)
    v.run(['git', 'checkout', '--detach', commit], 'checkout-merged-baseline')
    v.save('integration.json', {'sha': commit, 'upstream': upstream, 'base': base, 'upstream_merged': True, 'resolved_paths': conflicts})
    v.verify_pr()


def prepare_patch():
    path = 'packages/cli/src/ui/utils/compression-text.ts'
    replace(path, "} from '@qwen-code/qwen-code-core';", "} from '@qwen-code/qwen-code-core/core/turn.js';")
    anchor = '    case CompressionStatus.COMPRESSION_FAILED_API_ERROR:\n'
    replace(path, anchor, "    case CompressionStatus.COMPRESSION_FAILED_INPUT_TOO_LARGE:\n      return t(\n        '" + MESSAGE + "',\n      );\n" + anchor)
    path = 'packages/cli/src/ui/components/messages/CompressionMessage.tsx'
    replace(path, "import { Box, Text } from 'ink';", "import { Box, Text } from 'ink';\nimport { isCompressionFailureStatus } from '@qwen-code/qwen-code-core/core/turn.js';")
    replace(path, '            compression.isPending ? theme.text.accent : theme.status.success', '''            isPending
              ? theme.text.accent
              : isCompressionFailureStatus(compressionStatus)
                ? theme.status.error
                : theme.status.success''')
    path = 'packages/cli/src/ui/opentui/session-compaction.tsx'
    replace(path, "import { CompressionStatus } from '@qwen-code/qwen-code-core/core/turn.js';\nimport { t } from '../../i18n/index.js';", "import { isCompressionFailureStatus, type CompressionStatus } from '@qwen-code/qwen-code-core/core/turn.js';\nimport { getCompressionStatusText } from '../utils/compression-text.js';")
    target = v.WORK/path
    text = target.read_text()
    first = text.index('const COMPRESSION_NOT_BENEFICIAL_TOKEN_LIMIT = 50000;')
    last = text.index('\nexport interface CompactionView {', first)
    target.write_text(text[:first] + '''export function compactionText(props: CompactionViewProps): string {
  return getCompressionStatusText(props);
}
''' + text[last:])
    replace(path, '/** Parity colors: accent while pending, success once settled. */', '/** Accent while pending, error on failure, and success otherwise. */')
    replace(path, 'export function compactionView(props: CompactionViewProps): CompactionView {\n  return {', 'export function compactionView(props: CompactionViewProps): CompactionView {\n  const failed = !props.isPending && isCompressionFailureStatus(props.compressionStatus);\n  return {')
    replace(path, '    color: props.isPending ? C.accent : C.green,\n    markerColor: C.accent,', '    color: props.isPending ? C.accent : failed ? C.red : C.green,\n    markerColor: failed ? C.red : C.accent,')
    path = 'packages/cli/src/ui/opentui/session-compaction.test.ts'
    anchor = "  it('returns an empty text for null', () => {"
    replace(path, anchor, """  it('reports input admission failure instead of an empty compression row', () => {
    expect(compactionText(props({
      compressionStatus: CompressionStatus.COMPRESSION_FAILED_INPUT_TOO_LARGE,
    }))).toBe('""" + MESSAGE + """');
  });

""" + anchor)
    target = v.WORK/path
    text = target.read_text()
    index = text.rindex('\n});')
    target.write_text(text[:index] + '''
  it.each([
    CompressionStatus.COMPRESSION_FAILED_INPUT_TOO_LARGE,
    CompressionStatus.COMPRESSION_FAILED_API_ERROR,
    CompressionStatus.COMPRESSION_FAILED_EMPTY_SUMMARY,
    CompressionStatus.COMPRESSION_FAILED_OUTPUT_TRUNCATED,
    CompressionStatus.COMPRESSION_FAILED_TOKEN_COUNT_ERROR,
    CompressionStatus.COMPRESSION_FAILED_INFLATED_TOKEN_COUNT,
  ])('uses error colours for failed compression status %s', (compressionStatus) => {
    const view = compactionView(props({ compressionStatus }));
    expect(view.pending).toBe(false);
    expect(view.color).toBe(C.red);
    expect(view.markerColor).toBe(C.red);
    expect(view.text).not.toBe('');
  });

  it('keeps pending styling ahead of a previous failure status', () => {
    const view = compactionView(props({
      isPending: true,
      compressionStatus: CompressionStatus.COMPRESSION_FAILED_INPUT_TOO_LARGE,
    }));
    expect(view.pending).toBe(true);
    expect(view.color).toBe(C.accent);
    expect(view.markerColor).toBe(C.accent);
    expect(view.text).toBe('Compressing chat history');
  });
''' + text[index:])
    target = v.WORK/'packages/cli/src/ui/components/messages/CompressionMessage.test.tsx'
    text = target.read_text()
    index = text.rindex('\n});')
    target.write_text(text[:index] + '''
  it('renders a concrete explanation for input admission failure', () => {
    const properties = createCompressionProps({
      originalTokenCount: 128000,
      newTokenCount: 128000,
      compressionStatus: CompressionStatus.COMPRESSION_FAILED_INPUT_TOO_LARGE,
    });
    const { lastFrame } = render(<CompressionMessage {...properties} />);
    expect(lastFrame()?.replace(/\\s+/g, ' ')).toContain(
      'not enough room for the input and summary.',
    );
  });
''' + text[index:])
    translations = {
        'en': MESSAGE,
        'zh': '无法压缩聊天历史：上下文窗口无法同时容纳输入和摘要。',
        'zh-TW': '無法壓縮聊天歷史：上下文視窗無法同時容納輸入與摘要。',
        'ja': '入力と要約を収める領域が不足しているため、チャット履歴を圧縮できませんでした。',
        'ru': 'Не удалось сжать историю чата: недостаточно места для ввода и сводки.',
        'pt': 'Não foi possível comprimir o histórico do chat porque não há espaço suficiente para a entrada e o resumo.',
        'fr': 'Impossible de compresser l’historique du chat : il n’y a pas assez de place pour l’entrée et le résumé.',
        'de': 'Der Chatverlauf konnte nicht komprimiert werden, da nicht genügend Platz für die Eingabe und die Zusammenfassung vorhanden ist.',
        'ca': 'No s’ha pogut comprimir l’historial del xat perquè no hi ha prou espai per a l’entrada i el resum.',
    }
    anchor = "  'Could not compress chat history because the compression summary was truncated.':"
    for language, translation in translations.items():
        replace('packages/cli/src/i18n/locales/' + language + '.js', anchor,
                '  ' + json.dumps(MESSAGE, ensure_ascii=False) + ':\n    ' + json.dumps(translation, ensure_ascii=False) + ',\n' + anchor)
    v.run(['git', 'diff', '--check'], 'patch-preflight')
    patch = subprocess.check_output(['git', 'diff', '--binary'], cwd=v.WORK)
    v.PATCH.write_bytes(patch)
    v.save('generated-patch.json', {'sha': v.capture(['git', 'hash-object', str(v.PATCH)]), 'files': v.SPEC['files']})
    v.run(['git', 'restore', '--source=HEAD', '--worktree', '--', *v.SPEC['files']], 'restore-before-regression')
    v.run(['git', 'apply', '--include=' + v.SPEC['red_file'], str(v.PATCH)], 'apply-rendering-regression')


if __name__ == '__main__':
    prepare_merge()
    prepare_patch()
