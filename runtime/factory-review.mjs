import { setTimeout as sleep } from "node:timers/promises";

// Bound native reads as well as polling; an unresponsive app server must not
// keep the worker alive after cancellation or the review collection deadline.
function bounded(operation, signal) {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    Promise.resolve().then(operation).then(
      value => { signal.removeEventListener("abort", abort); resolve(value); },
      error => { signal.removeEventListener("abort", abort); reject(error); },
    );
  });
}

// Read native Codex metadata/results after the coordinator finishes. OpenHands
// does not advertise native child sessions, so ACP's capability-dependent wait
// cannot establish reviewer completion here. Match its ten-minute wait budget.
// [impl->req~im-review-evidence~1]
export async function emitFactoryReviewEvidence(client, update, threadId, turnId, { signal, timeoutMs = 600_000, pollMs = 1000 } = {}) {
  const event = {
    sessionUpdate: "tool_call",
    toolCallId: `factory-review:${threadId}:${turnId}`,
    title: "Factory specialist review",
    kind: "other",
    rawInput: { version: 2, threadId, turnId },
  };
  const deadline = AbortSignal.timeout(timeoutMs);
  const waitSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
  let agents = [];
  try {
    const read = async id => (await bounded(() => client.codexClient.threadReadWithHistory(id), waitSignal)).thread;
    const root = await read(threadId);
    // The adapter clears currentTurnId when it receives turn/completed.
    event.rawInput.turnId = root.turns.at(-1)?.id ?? turnId;
    event.toolCallId = `factory-review:${threadId}:${event.rawInput.turnId}`;
    const children = new Set();
    for (const turn of root.turns) {
      for (const item of turn.items) {
        if (item.type === "collabAgentToolCall" && item.tool === "spawnAgent" && item.status === "completed") {
          for (const id of item.receiverThreadIds) children.add(id);
        }
        if (item.type === "subAgentActivity" && item.kind !== "interrupted" && item.agentThreadId !== threadId) {
          children.add(item.agentThreadId);
        }
      }
    }
    const collect = async id => {
      const child = await read(id);
      const turn = child.turns.at(-1);
      const message = turn?.items.filter((item) => item.type === "agentMessage" && item.phase === "final_answer").at(-1);
      return {
        thread_id: child.id,
        parent_thread_id: child.parentThreadId ?? child.source?.subAgent?.thread_spawn?.parent_thread_id,
        role: child.agentRole ?? child.source?.subAgent?.thread_spawn?.agent_role,
        status: turn?.status ?? "missing",
        message: message?.text ?? "",
        error: turn?.error?.message ?? null,
      };
    };
    agents = await Promise.all([...children].map(collect));
    // Invalid identities/roles and terminal failures go straight to the existing
    // validator. Never start a replacement reviewer or trust coordinator prose.
    while (agents.length === 1 && agents[0].role === "Alibaba Reviewer"
        && agents[0].parent_thread_id === threadId && agents[0].thread_id !== threadId
        && agents[0].status === "inProgress") {
      if (event.sessionUpdate === "tool_call") {
        await update({ ...event, status: "in_progress", rawOutput: { agents } });
        event.sessionUpdate = "tool_call_update";
      }
      await sleep(pollMs, undefined, { signal: waitSignal });
      agents = await Promise.all([...children].map(collect));
    }
    waitSignal.throwIfAborted();
    await update({ ...event, status: "completed", rawOutput: { agents } });
  } catch (error) {
    const reason = signal?.aborted ? "Native specialist collection cancelled."
      : deadline.aborted ? "Timed out waiting for native specialist completion."
      : `Could not read native specialist results: ${error.message}`;
    await update({ ...event, status: "failed", rawOutput: { agents, error: reason } });
  }
}
