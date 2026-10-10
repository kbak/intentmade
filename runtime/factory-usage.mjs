// Provider counters describe the root thread. They do not prove billed spend or
// coverage of delegated threads. Keep missing/reset baselines explicitly unknown.
import { randomUUID } from 'node:crypto';
const fields = ['totalTokens', 'inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens'];
const copy = (value) => value == null ? null : Object.fromEntries(fields.map(k => [k, value[k]]));
const valid = (value) => value != null && fields.every(k => Number.isSafeInteger(value[k]) && value[k] >= 0)
  && value.cachedInputTokens <= value.inputTokens && value.reasoningOutputTokens <= value.outputTokens;
const zero = () => Object.fromEntries(fields.map(k => [k, 0]));

export function beginFactoryUsage(state) {
  state.factoryUsage = {
    id: randomUUID(), baseline: copy(state.factoryRawTotal ?? (state.factoryFreshSession ? zero() : null)),
    latest: null, last: null, notifications: 0, reason: null, response: null,
  };
  state.factoryFreshSession = false;
}

export function observeFactoryUsage(state, params) {
  if (params.threadId !== state.sessionId) return;
  const total = copy(params.tokenUsage?.total);
  const last = copy(params.tokenUsage?.last);
  const usage = state.factoryUsage;
  if (usage) {
    const previous = usage.latest ?? usage.baseline;
    if (!valid(total)) usage.reason = 'invalid_counter';
    else if (previous && fields.some(k => total[k] < previous[k])) usage.reason = 'counter_reset';
    usage.latest = total;
    usage.last = last;
    usage.notifications += 1;
  }
  state.factoryRawTotal = valid(total) ? total : null;
}

export function factoryUsageSnapshot(state) {
  const usage = state.factoryUsage;
  if (!usage) return { status: 'unknown', reason: 'no_active_prompt', delta: null };
  if (usage.response) return usage.response;
  let reason = usage.reason;
  if (!reason && !valid(usage.baseline)) reason = 'missing_baseline';
  if (!reason && !valid(usage.latest)) reason = 'missing_notifications';
  const delta = reason ? null : Object.fromEntries(fields.map(k => [k, usage.latest[k] - usage.baseline[k]]));
  // A malformed provider relationship must not reach the SDK as negative usage.
  if (delta && !valid(delta)) reason = 'invalid_delta';
  return {
    version: 1, id: usage.id, semantics: 'root_thread_cumulative_delta',
    status: reason ? 'unknown' : 'observed', reason,
    baseline: copy(usage.baseline), latest: copy(usage.latest), last: copy(usage.last),
    notifications: usage.notifications, delta: reason ? null : delta,
    delegated_usage: null, billed_cost: null,
  };
}

export async function endFactoryUsage(state, update, cancelled) {
  const snapshot = factoryUsageSnapshot(state);
  const lateCounters = JSON.stringify(snapshot.latest) !== JSON.stringify(state.factoryRawTotal);
  try {
    await update({
      sessionUpdate: 'tool_call', toolCallId: `factory-usage:${snapshot.id}`,
      title: 'Factory provider usage', kind: 'other', status: 'completed',
      rawInput: { version: 1 },
      rawOutput: { ...snapshot, cancelled, late_counters: lateCounters,
        counters_at_cleanup: copy(state.factoryRawTotal),
        sdk_response_emitted: Boolean(state.factoryUsage?.response),
        coverage: 'observed notifications only; delegated coverage and billed cost unknown' },
    });
  } finally {
    state.factoryUsage = null;
  }
}
