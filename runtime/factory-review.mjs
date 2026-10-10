// Read native Codex metadata/results after the coordinator finishes. The model's
// summary is not evidence that the required roles ran or that their work passed.
export async function emitFactoryReviewEvidence(client, update, threadId, turnId) {
  const event = {
    sessionUpdate: "tool_call",
    toolCallId: `factory-review:${threadId}:${turnId}`,
    title: "Factory specialist review",
    kind: "other",
    rawInput: { version: 2, threadId, turnId },
  };
  try {
    const root = (await client.codexClient.threadReadWithHistory(threadId)).thread;
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
    const agents = await Promise.all([...children].map(async (id) => {
      const child = (await client.codexClient.threadReadWithHistory(id)).thread;
      const turn = child.turns.at(-1);
      const message = turn?.items.filter((item) => item.type === "agentMessage" && item.phase === "final_answer").at(-1);
      return {
        thread_id: child.id,
        parent_thread_id: child.parentThreadId ?? child.source?.subAgent?.thread_spawn?.parent_thread_id,
        role: child.agentRole ?? child.source?.subAgent?.thread_spawn?.agent_role,
        status: turn?.status ?? "missing",
        message: message?.text ?? "",
      };
    }));
    await update({ ...event, status: "completed", rawOutput: { agents } });
  } catch (error) {
    await update({ ...event, status: "failed", rawOutput: { error: `Could not read native specialist results: ${error.message}` } });
  }
}
