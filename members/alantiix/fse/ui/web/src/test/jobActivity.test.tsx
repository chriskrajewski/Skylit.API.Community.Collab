import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook, screen } from '@testing-library/react';
import { JobActivity, useLastLineAt } from '../components/JobActivity';
import type { Job } from '../lib/types';

const NOW_MS = Date.UTC(2026, 9, 12, 15, 0, 0);
const NOW_S = NOW_MS / 1000;

function job(over: Partial<Job> = {}): Job {
  return {
    id: '4f3d6bc468b23574', program: 'fse', command: ['pull'], argv: ['fse', 'pull'], placement: 'batch',
    executor: 'ecs', status: 'running', exit_code: null, exit_meaning: null, title: 'pull estimate',
    user: 'operator@localhost', created_at: NOW_S - 310, started_at: NOW_S - 300, stopped_at: null,
    run_dir: null, external_id: null, ...over,
  };
}

const row = () => screen.getByTestId('job-activity');

describe('JobActivity', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: NOW_MS });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows elapsed time and the silence, with a warning dot after a minute of no output', () => {
    render(<JobActivity job={job()} lastLineAt={NOW_MS - 90_000} />);
    expect(row()).toHaveTextContent('Running · 5 min 0 s elapsed · last output 1 min 30 s ago');
    expect(row()).toHaveAttribute('aria-live', 'off');
    expect(row().querySelector('.fse-dot--warning')).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(row()).toHaveTextContent('5 min 1 s elapsed · last output 1 min 31 s ago');
  });

  it('is live (no warning) while output is recent', () => {
    render(<JobActivity job={job()} lastLineAt={NOW_MS - 5_000} />);
    expect(row()).toHaveTextContent('last output 5 s ago');
    expect(row().querySelector('.fse-dot--live')).not.toBeNull();
    expect(row().querySelector('.fse-dot--warning')).toBeNull();
  });

  it('says it is waiting before the first line', () => {
    render(<JobActivity job={job()} lastLineAt={null} />);
    expect(row()).toHaveTextContent('Running · 5 min 0 s elapsed · waiting for the first output');
  });

  it('renders nothing once the job is done', () => {
    const { container } = render(<JobActivity job={job({ status: 'succeeded', stopped_at: NOW_S })} lastLineAt={NOW_MS} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('useLastLineAt', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: NOW_MS });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('moves only when the log gains a line, and resets for another job', () => {
    const { result, rerender } = renderHook(({ id, lines }) => useLastLineAt(id, lines), {
      initialProps: { id: 'a', lines: [] as string[] },
    });
    expect(result.current).toBeNull();
    rerender({ id: 'a', lines: ['task x: RUNNING'] });
    expect(result.current).toBe(NOW_MS);
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    rerender({ id: 'a', lines: ['task x: RUNNING'] }); // a poll with the same log: no new output
    expect(result.current).toBe(NOW_MS);
    rerender({ id: 'a', lines: ['task x: RUNNING', 'fse-entrypoint: downloading cache ...'] });
    expect(result.current).toBe(NOW_MS + 30_000);
    rerender({ id: 'b', lines: [] });
    expect(result.current).toBeNull();
  });

  it('ignores the API heartbeat lines, so a long silence keeps counting', () => {
    const real = ['task e66f: RUNNING', 'fse-entrypoint: downloading data cache ...'];
    const { result, rerender } = renderHook(({ lines }) => useLastLineAt('a', lines), {
      initialProps: { lines: real },
    });
    expect(result.current).toBe(NOW_MS);
    act(() => {
      vi.advanceTimersByTime(61_000);
    });
    const beat1 = 'task e66f: still RUNNING, no new output for 1m00s';
    rerender({ lines: [...real, beat1] });
    expect(result.current).toBe(NOW_MS);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    const beat2 = 'task e66f: still RUNNING, no new output for 2m00s';
    rerender({ lines: [...real, beat1, beat2] });
    expect(result.current).toBe(NOW_MS);
    // real output after the heartbeats moves it again
    rerender({ lines: [...real, beat1, beat2, 'fse-entrypoint: cache downloaded in 2m05s'] });
    expect(result.current).toBe(NOW_MS + 121_000);
  });

  it('keeps the warning dot on through heartbeats', () => {
    const lines = ['task e66f: RUNNING'];
    const { result, rerender } = renderHook(({ l }) => useLastLineAt('a', l), { initialProps: { l: lines } });
    act(() => {
      vi.advanceTimersByTime(64_000);
    });
    rerender({ l: [...lines, 'task e66f: still RUNNING, no new output for 1m00s'] });
    render(<JobActivity job={job()} lastLineAt={result.current} />);
    expect(row()).toHaveTextContent('last output 1 min 4 s ago');
    expect(row().querySelector('.fse-dot--warning')).not.toBeNull();
  });
});
