import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { filterEvents, paginateNewest } from '../server/events.ts'
import { changeInfo } from '../server/change-info.ts'
import { findProjectRoot, isSafeSegment, resolvePathInside } from '../server/path-utils.ts'
import { buildAutoExpandSet, buildTree, findFirstInProgress } from '../src/composables/buildTree.ts'
import { aggregateStatuses, finalGateStatus } from '../src/composables/status.ts'
import { usePolling } from '../src/composables/usePolling.ts'
import { countSnapshotPhases } from '../src/shared/pipelineStatus.ts'
import { formatPipelineTimestamp } from '../src/shared/dateTime.ts'
import { enrichSnapshotPhaseTelemetry } from '../server/phase-telemetry.ts'

test('paginateNewest returns the latest page first', () => {
  assert.deepEqual(paginateNewest([1, 2, 3, 4, 5], 1, 2), [5, 4])
  assert.deepEqual(paginateNewest([1, 2, 3, 4, 5], 2, 2), [3, 2])
})

test('pipeline timestamps use a readable log format', () => {
  assert.equal(formatPipelineTimestamp('2026-07-31T11:03:06+08:00'), '2026-07-31 11:03:06')
  assert.equal(formatPipelineTimestamp('2026-07-31T11:03:06.123Z'), '2026-07-31 11:03:06')
  assert.equal(formatPipelineTimestamp(null), '-')
  assert.equal(formatPipelineTimestamp('unknown'), 'unknown')
})

test('resolvePathInside rejects traversal and absolute paths', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pg-monitor-'))
  fs.mkdirSync(path.join(root, '2-build'))
  fs.writeFileSync(path.join(root, '2-build', 'report.md'), 'ok')
  assert.equal(resolvePathInside(path.join(root, '2-build'), 'report.md'), path.join(root, '2-build', 'report.md'))
  assert.throws(() => resolvePathInside(path.join(root, '2-build'), '..\\project.yaml'))
  assert.throws(() => resolvePathInside(path.join(root, '2-build'), path.resolve(root, 'secret.txt')))
})

test('project root requires .pg/project.yaml', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pg-project-'))
  const nested = path.join(root, 'a', 'b')
  fs.mkdirSync(path.join(root, '.pg'), { recursive: true })
  fs.mkdirSync(nested, { recursive: true })
  fs.writeFileSync(path.join(root, '.pg', 'project.yaml'), 'schema: test')
  assert.equal(findProjectRoot(nested), root)
  assert.throws(() => findProjectRoot(os.tmpdir(), path.join(root, 'missing')))
})

test('change names must be a single safe segment', () => {
  assert.equal(isSafeSegment('2026-07-31-add-api'), true)
  assert.equal(isSafeSegment('../secret'), false)
  assert.equal(isSafeSegment('a/b'), false)
})

test('aggregateStatuses reflects child execution', () => {
  assert.equal(aggregateStatuses(['completed', 'pass']), 'completed')
  assert.equal(aggregateStatuses(['completed', 'pending']), 'in_progress')
  assert.equal(aggregateStatuses(['completed', 'failed']), 'failed')
})

test('finalGateStatus supports explicit and legacy snapshots', () => {
  assert.equal(finalGateStatus({ tracks: { 'final-gate': { phases: { gate: { status: 'pass' } } } } }), 'pass')
  assert.equal(finalGateStatus({ status: 'completed', pipeline_order: ['dev.a', 'final-gate'] }), 'pass')
  assert.equal(finalGateStatus({ status: 'in_progress', current_track: 'final-gate' }), 'in_progress')
})

test('filterEvents searches the complete event set before pagination', () => {
  const events = [
    { type: 'dispatch_started', track: 'dev.api' },
    { type: 'workflow_failed', summary: 'database unavailable' },
    { type: 'track_completed', track: 'dev.web' },
  ]
  assert.deepEqual(filterEvents(events, 'database'), [events[1]])
  assert.deepEqual(filterEvents(events, '', true), [events[1]])
})

test('filterEvents ignores hidden snapshot state', () => {
  const matching = {
    type: 'record_received',
    data: { track: 'final-gate', phase: 'gate', summary: 'final gate passed' },
  }
  const unrelated = {
    type: 'record_received',
    data: {
      track: 'int.links',
      phase: 'test',
      summary: 'integration tests passed',
    },
    snapshot_after: {
      current_track: 'final-gate',
      tracks: { 'final-gate': { phases: { gate: { status: 'pending' } } } },
    },
  }

  assert.deepEqual(filterEvents([matching, unrelated], 'final-gate'), [matching])
  assert.deepEqual(filterEvents([matching, unrelated], 'integration'), [unrelated])
})

test('exact track or phase names take priority over summary matches', () => {
  const trackMatch = { type: 'dispatch_started', data: { track: 'final-gate', phase: 'gate' } }
  const phaseMatch = { type: 'record_received', data: { track: 'dev.api', phase: 'gate' } }
  const summaryOnly = { type: 'git_commit', data: { message: 'auto-record final-gate:gate pass' } }

  assert.deepEqual(filterEvents([trackMatch, phaseMatch, summaryOnly], 'final-gate'), [trackMatch])
  assert.deepEqual(filterEvents([trackMatch, phaseMatch, summaryOnly], 'gate'), [trackMatch, phaseMatch])
  assert.deepEqual(filterEvents([trackMatch, phaseMatch, summaryOnly], 'auto-record'), [summaryOnly])
})

test('failure filtering reads nested event data', () => {
  const failed = { type: 'record_received', data: { status: 'failed', summary: 'tests failed' } }
  const passed = { type: 'record_received', data: { status: 'completed', summary: 'tests passed' } }
  assert.deepEqual(filterEvents([failed, passed], '', true), [failed])
})

test('snapshot phase telemetry is reconstructed from pipeline events', () => {
  const snapshot = {
    tracks: {
      'dev.api': {
        phases: {
          test: { agent: null, started_at: null, completed_at: null },
        },
      },
    },
  }
  const events = [
    { ts: '2026-08-05T10:00:00+08:00', type: 'dispatch_started', data: { track: 'dev.api', phase: 'test', agent: 'pg-build/test' } },
    { ts: '2026-08-05T10:02:00+08:00', type: 'record_received', data: { track: 'dev.api', phase: 'test' } },
  ]

  const enriched = enrichSnapshotPhaseTelemetry(snapshot, events)
  assert.deepEqual(enriched.tracks['dev.api'].phases.test, {
    agent: 'pg-build/test',
    started_at: '2026-08-05T10:00:00+08:00',
    completed_at: '2026-08-05T10:02:00+08:00',
  })
  assert.equal(snapshot.tracks['dev.api'].phases.test.agent, null)
})

test('final gate display state is synthesized from events when absent from snapshot tracks', () => {
  const snapshot = {
    status: 'completed',
    pipeline_order: ['dev.api', 'final-gate'],
    tracks: {},
  }
  const events = [
    { ts: '2026-08-05T10:00:00+08:00', type: 'dispatch_started', data: { track: 'final-gate', phase: 'gate', agent: 'pg-build/gate' } },
    { ts: '2026-08-05T10:03:00+08:00', type: 'record_received', data: { track: 'final-gate', phase: 'gate', status: 'pass', summary: 'all tracks passed', report_path: '011-final-gate-report.md' } },
  ]

  const enriched = enrichSnapshotPhaseTelemetry(snapshot, events)
  assert.deepEqual(enriched.tracks['final-gate'].phases.gate, {
    status: 'pass',
    attempt: 1,
    started_at: '2026-08-05T10:00:00+08:00',
    completed_at: '2026-08-05T10:03:00+08:00',
    agent: 'pg-build/gate',
    report_path: '011-final-gate-report.md',
    summary: 'all tracks passed',
    tasks_marked: [],
    cycles: [],
    fix_cycles: [],
    review_fix_cycles: [],
    gate_cycles: [],
    current_cycle: 1,
  })
})

test('final gate is a selectable leaf with event-derived details', () => {
  const manifest = {
    stages: [],
    final_gate: { tasks_md_section: '11. final-gate - 最终门控审查' },
  }
  const snapshot = {
    tracks: {
      'final-gate': {
        status: 'pass',
        phases: {
          gate: {
            status: 'pass',
            agent: 'pg-build/gate',
            started_at: '2026-08-05T10:00:00+08:00',
            completed_at: '2026-08-05T10:03:00+08:00',
          },
        },
      },
    },
  }

  const tree = buildTree(manifest as never, snapshot as never)
  const finalStage = tree.at(-1)!
  const finalGate = finalStage.children[0]
  assert.equal(finalStage.label, 'final')
  assert.equal(finalGate.type, 'final-gate')
  assert.equal(finalGate.label, 'final-gate')
  assert.equal(finalGate.children.length, 0)
  assert.equal((finalGate.meta?.phaseState as { agent: string }).agent, 'pg-build/gate')
})

test('latest dispatch wins when a phase is retried', () => {
  const snapshot = { tracks: { 'dev.api': { phases: { gate: {} } } } }
  const events = [
    { ts: '10:00', type: 'dispatch_started', data: { track: 'dev.api', phase: 'gate', agent: 'old-agent' } },
    { ts: '10:01', type: 'record_received', data: { track: 'dev.api', phase: 'gate' } },
    { ts: '10:02', type: 'dispatch_started', data: { track: 'dev.api', phase: 'gate', agent: 'pg-build/gate' } },
    { ts: '10:03', type: 'record_received', data: { track: 'dev.api', phase: 'gate' } },
  ]

  const phase = enrichSnapshotPhaseTelemetry(snapshot, events).tracks['dev.api'].phases.gate
  assert.deepEqual(phase, {
    agent: 'pg-build/gate',
    started_at: '10:02',
    completed_at: '10:03',
  })
})

test('snapshot telemetry remains authoritative when already populated', () => {
  const phase = { agent: 'snapshot-agent', started_at: 'snapshot-start', completed_at: 'snapshot-end' }
  const snapshot = { tracks: { 'dev.api': { phases: { dev: phase } } } }
  const events = [
    { ts: 'event-start', type: 'dispatch_started', data: { track: 'dev.api', phase: 'dev', agent: 'event-agent' } },
    { ts: 'event-end', type: 'record_received', data: { track: 'dev.api', phase: 'dev' } },
  ]

  assert.deepEqual(enrichSnapshotPhaseTelemetry(snapshot, events).tracks['dev.api'].phases.dev, phase)
})

test('countSnapshotPhases is shared by server and client progress', () => {
  assert.deepEqual(countSnapshotPhases({
    tracks: {
      'dev.api': { phases: { test: { status: 'pass' }, dev: { status: 'completed' }, gate: { status: 'pending' } } },
    },
  }), { completed: 2, total: 3 })
})

test('findFirstInProgress reaches nested fix sub-phases', () => {
  const tree = [{
    id: 'stage', label: 'stage', type: 'stage', status: 'in_progress', meta: {}, children: [{
      id: 'track', label: 'track', type: 'track', status: 'in_progress', meta: {}, children: [{
        id: 'phase', label: 'phase', type: 'phase', status: 'in_progress', meta: {}, children: [{
          id: 'cycle', label: 'cycle', type: 'fix-cycle', status: 'in_progress', meta: {}, children: [{
            id: 'sub-phase', label: 'sub-phase', type: 'sub-phase', status: 'in_progress', meta: {}, children: [],
          }],
        }],
      }],
    }],
  }]
  assert.equal(findFirstInProgress(tree as never), 'sub-phase')
  const expanded = buildAutoExpandSet(tree as never)
  for (const id of ['stage', 'track', 'phase', 'cycle']) assert.equal(expanded.has(id), true)
})

test('usePolling never overlaps slow requests', async () => {
  let active = 0
  let maxActive = 0
  let calls = 0
  const sleep = (milliseconds: number) => new Promise(resolve => setTimeout(resolve, milliseconds))
  const polling = usePolling(async () => {
    active += 1
    maxActive = Math.max(maxActive, active)
    calls += 1
    await sleep(20)
    active -= 1
  }, 5)

  polling.start()
  await sleep(75)
  polling.stop()
  await sleep(25)
  assert.equal(maxActive, 1)
  assert.ok(calls >= 2)
})

test('changeInfo uses a configurable stalled threshold', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pg-change-'))
  try {
    fs.mkdirSync(path.join(root, '2-build'), { recursive: true })
    fs.writeFileSync(path.join(root, 'execution-manifest.yaml'), 'change: sample')
    fs.writeFileSync(path.join(root, '2-build', 'pipeline.snapshot.json'), JSON.stringify({
      status: 'running',
      current_stage: 'dev',
      current_track: 'dev.api',
      current_phase: 'test',
      tracks: { 'dev.api': { phases: { test: { status: 'running' } } } },
    }))
    const old = new Date(Date.now() - 10_000)
    fs.utimesSync(path.join(root, 'execution-manifest.yaml'), old, old)
    fs.utimesSync(path.join(root, '2-build', 'pipeline.snapshot.json'), old, old)
    assert.equal(changeInfo('sample', root, true, 1_000).isStalled, true)
    assert.equal(changeInfo('sample', root, true, 60_000).isStalled, false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
