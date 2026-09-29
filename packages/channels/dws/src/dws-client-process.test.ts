/**
 * @license
 * Copyright 2026 Qwen Team
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFile } from 'node:child_process';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DwsClient, DwsCommandError } from './dws-client.js';

vi.mock('node:child_process', () => ({ execFile: vi.fn() }));

describe('DWS command process', () => {
  function mockFailedDwsCommand({
    code = 1,
    stdout = '',
    stderr = '',
    signal,
    killed = false,
  }: {
    code?: unknown;
    stdout?: string;
    stderr?: string;
    signal?: NodeJS.Signals;
    killed?: boolean;
  }) {
    vi.mocked(execFile).mockImplementation(((
      _file,
      _args,
      _options,
      callback,
    ) => {
      queueMicrotask(() => {
        callback(
          Object.assign(new Error('exit'), { code, signal, killed }),
          stdout,
          stderr,
        );
      });
      return {
        exitCode: typeof code === 'number' ? code : null,
        kill: vi.fn(),
      };
    }) as typeof execFile);
  }

  afterEach(() => {
    vi.useRealTimers();
    vi.mocked(execFile).mockReset();
  });

  it('escalates a timed-out command to SIGKILL', async () => {
    vi.useFakeTimers();
    let callback!: (
      error: NodeJS.ErrnoException | null,
      stdout: string,
      stderr: string,
    ) => void;
    const child = {
      exitCode: null as number | null,
      kill: vi.fn((signal: NodeJS.Signals) => {
        if (signal === 'SIGKILL') {
          child.exitCode = 1;
          callback(
            Object.assign(new Error('killed'), {
              code: null,
              signal: 'SIGKILL',
              killed: true,
            }),
            '',
            '',
          );
        }
        return true;
      }),
    };
    vi.mocked(execFile).mockImplementation(((
      _file,
      _args,
      _options,
      receivedCallback,
    ) => {
      callback = receivedCallback;
      return child;
    }) as typeof execFile);

    const result = new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(50_000);

    expect(child.kill).toHaveBeenCalledWith('SIGKILL');
    const error = await result;
    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toBe('DWS version failed (SIGKILL).');
  });

  it('includes sanitized stderr details when a DWS command exits non-zero', async () => {
    mockFailedDwsCommand({
      stderr: '\u001b[31mHTTP 400\n{"errorCode":"InvalidArgs"}\u001b[0m',
    });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toContain(
      'DWS version failed (1): HTTP 400 {"errorCode":"InvalidArgs"}',
    );
    expect((error as Error).message).not.toContain('[31m');
  });

  it('uses stdout details when stderr is empty', async () => {
    mockFailedDwsCommand({ stdout: 'detail on stdout' });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toContain(
      'DWS version failed (1): detail on stdout',
    );
  });

  it('prefers stderr details over stdout details', async () => {
    mockFailedDwsCommand({
      stdout: 'stdout noise',
      stderr: 'real error',
    });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toBe('DWS version failed (1): real error');
  });

  it('falls back to stdout when stderr sanitizes to empty', async () => {
    mockFailedDwsCommand({
      stdout: 'error: quota exceeded',
      stderr: '\u001b[2K\r',
    });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toContain(
      'DWS version failed (1): error: quota exceeded',
    );
  });

  it('uses a bare message when stderr and stdout sanitize to empty', async () => {
    mockFailedDwsCommand({ stderr: '\u001b[2K\r' });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toBe('DWS version failed (1).');
  });

  it('strips OSC terminal control sequences from failure details', async () => {
    mockFailedDwsCommand({
      stderr: `${String.fromCharCode(27)}]0;evil${String.fromCharCode(7)}boom`,
    });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toContain('boom');
    expect((error as Error).message).not.toContain(']0;evil');
  });

  it('caps failure details before exposing them in the error message', async () => {
    mockFailedDwsCommand({ stderr: `${'x'.repeat(300)}tail` });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    const prefix = 'DWS version failed (1): ';
    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toBe(
      `${prefix}…${'x'.repeat(200 - prefix.length - 5)}tail`,
    );
    expect(Array.from((error as Error).message)).toHaveLength(200);
  });

  it('keeps later diagnostics from long command output', async () => {
    mockFailedDwsCommand({ stderr: `${'noise'.repeat(200)}fatal: denied` });

    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(DwsCommandError);
    expect((error as DwsCommandError).outcome).toBe('unknown');
    expect((error as Error).message).toContain('fatal: denied');
  });

  it('strips a long OSC payload before selecting the diagnostic tail', async () => {
    mockFailedDwsCommand({
      stderr: `fatal: denied\u001b]0;${'secret-title'.repeat(100)}\u0007`,
    });
    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);
    expect((error as Error).message).toBe(
      'DWS version failed (1): fatal: denied',
    );
    expect((error as Error).message).not.toContain('secret-title');
  });

  it('preserves a complete Unicode tail within the downstream 200-character cap', async () => {
    mockFailedDwsCommand({ stderr: `${'🎉'.repeat(600)} fatal: 拒绝` });
    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);
    const message = (error as Error).message;
    expect(Array.from(message)).toHaveLength(200);
    expect(message).toContain('…');
    expect(message.endsWith(' fatal: 拒绝')).toBe(true);
    expect(message).not.toMatch(/\p{Surrogate}/u);
  });

  it('does not spend the diagnostic budget on control-character padding', async () => {
    mockFailedDwsCommand({
      stderr: `fatal: denied${'\n\r\t\u0000'.repeat(1000)}`,
    });
    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);
    expect((error as Error).message).toBe(
      'DWS version failed (1): fatal: denied',
    );
  });

  it.each([
    { code: null, signal: 'SIGTERM' as const, killed: true },
    { code: null, signal: 'SIGKILL' as const, killed: true },
    { code: 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' },
    { code: 'ABORT_ERR' },
  ])('does not log interrupted stdout for $code/$signal', async (failure) => {
    mockFailedDwsCommand({ ...failure, stdout: 'private document body' });
    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);
    expect((error as Error).message).not.toContain('private document body');
    expect((error as Error).message).toContain(
      String(failure.code ?? failure.signal),
    );
    expect((error as DwsCommandError).outcome).toBe('unknown');
  });

  it('does not copy a structured stdout payload into a failure log', async () => {
    mockFailedDwsCommand({ stdout: '{"document":"private document body"}' });
    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);
    expect((error as Error).message).toBe('DWS version failed (1).');
  });

  it('identifies the command without including profile names or message contents', async () => {
    mockFailedDwsCommand({ stderr: 'permission denied' });
    const client = new DwsClient({
      executable: '/opt/dws',
      profile: 'private-profile',
    });
    const error = await client
      .sendImMessage(
        { kind: 'group', conversationId: 'private-room' },
        'private message',
        'private-key',
      )
      .catch((caught: unknown) => caught);
    expect((error as Error).message).toBe(
      'DWS chat message send failed (1): permission denied',
    );
    expect((error as Error).message).not.toContain('private');
    expect((error as DwsCommandError).outcome).toBe('unknown');
  });

  it('preserves not_sent for spawn failures', async () => {
    mockFailedDwsCommand({ code: 'ENOENT' });
    const error = await new DwsClient({ executable: '/opt/dws' })
      .assertCompatible()
      .catch((caught: unknown) => caught);
    expect((error as DwsCommandError).outcome).toBe('not_sent');
    expect((error as Error).message).toContain('ENOENT');
  });
});
