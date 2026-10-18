"""Exercise native result collection without a model or active factory."""

import json
import subprocess
import unittest
from pathlib import Path

import review
from review_fixture import evidence

RUNTIME = Path(__file__).resolve().parent.parent / "runtime"
if not RUNTIME.is_dir():
    RUNTIME = Path("/runtime")


class ReviewCompletionTests(unittest.TestCase):
    def test_pending_reviewer_finishes_and_collection_is_bounded(self):
        script = r"""
import assert from 'node:assert/strict';
import {emitFactoryReviewEvidence} from MODULE;
const root = {id:'root', turns:[{id:'turn',items:[{type:'collabAgentToolCall',
  tool:'spawnAgent',status:'completed',receiverThreadIds:['child']}]}]};
const child = (status, extra={}) => ({id:'child',parentThreadId:'root',agentRole:'Alibaba Reviewer',
  turns:[{status,items:status==='completed' ? [{type:'agentMessage',phase:'final_answer',text:'{"verdict":"PASS"}'}] : [],...extra}]});
async function run(readChild, options={}, parent=root) {
  const events=[];
  const client={codexClient:{threadReadWithHistory:async id=>({thread:id==='root'?parent:await readChild()})}};
  await emitFactoryReviewEvidence(client,async e=>events.push(e),'root',null,{pollMs:1,timeoutMs:1000,...options});
  return events;
}
// Keep the test process alive even when a deliberately hung read is its only work.
const keepAlive=setInterval(()=>{},1000);
try {
  let reads=0;
  let events=await run(()=>child(++reads<3?'inProgress':'completed'));
  assert.equal(reads,3); assert.equal(events[0].status,'in_progress');
  assert.equal(events.at(-1).status,'completed');
  assert.equal(events[0].sessionUpdate,'tool_call');
  assert.equal(events.at(-1).sessionUpdate,'tool_call_update');
  assert.equal(events.length,2);
  assert.equal(events.at(-1).rawOutput.agents[0].message,'{"verdict":"PASS"}');
  assert.equal(events.at(-1).rawInput.turnId,'turn');
  for (const state of ['failed','interrupted','missing']) {
    reads=0; events=await run(()=>{reads++;return child(state,{error:{message:'provider unavailable'}})});
    assert.equal(reads,1); assert.equal(events.at(-1).rawOutput.agents[0].status,state);
    assert.equal(events.at(-1).rawOutput.agents[0].error,'provider unavailable');
  }
  events=await run(()=>child('inProgress'),{timeoutMs:20});
  assert.equal(events.at(-1).status,'failed');
  assert.match(events.at(-1).rawOutput.error,/Timed out/);
  assert.equal(events.at(-1).rawOutput.agents[0].status,'inProgress');
  // A hung metadata request shares the deadline; cancellation also interrupts it.
  events=await run(()=>new Promise(()=>{}),{timeoutMs:20});
  assert.match(events.at(-1).rawOutput.error,/Timed out/);
  const abort=new AbortController();
  const pending=run(()=>new Promise(()=>{}),{signal:abort.signal});
  setTimeout(()=>abort.abort(),10); events=await pending;
  assert.match(events.at(-1).rawOutput.error,/cancelled/);
  const cancelled=new AbortController(); cancelled.abort(); reads=0;
  events=await run(()=>{reads++;return child('completed')},{signal:cancelled.signal});
  assert.equal(reads,0); assert.equal(events.at(-1).status,'failed');
  events=await run(()=>{throw new Error('native read failed')});
  assert.match(events.at(-1).rawOutput.error,/native read failed/);
  for (const override of [{agentRole:'unexpected'},{parentThreadId:'unrelated'},{id:'root'}]) {
    reads=0; events=await run(()=>{reads++;return {...child('inProgress'),...override}});
    assert.equal(reads,1); assert.equal(events.length,1);
  }
  events=await run(()=>child('completed'),{}, {id:'root',turns:[{id:'turn',items:[]}]});
  assert.deepEqual(events.at(-1).rawOutput.agents,[]);
  events=await run(()=>child('completed',{items:[]}));
  assert.equal(events.at(-1).rawOutput.agents[0].message,'');
} finally { clearInterval(keepAlive); }
""".replace("MODULE", json.dumps((RUNTIME / "factory-review.mjs").as_uri()))
        # [utest~im-native-review-completion~1->req~im-review-evidence~1]
        subprocess.run(
            ["/acp-node/bin/node", "--input-type=module", "-e", script],
            check=True,
            timeout=15,
        )

    def test_collection_and_native_failure_reasons_reach_blocked_result(self):
        event = evidence()
        event["status"] = "failed"
        event["raw_output"]["error"] = "Timed out waiting for native specialist completion."
        result = review.evaluate([event])
        # [utest~im-native-review-diagnostics~1->req~im-review-evidence~1]
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertIn("Timed out waiting", result.summary)
        event = evidence()
        event["raw_output"]["agents"][0].update(status="failed", error="Provider at capacity")
        result = review.evaluate([event])
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertIn("state: failed", result.summary)
        self.assertIn("Provider at capacity", result.summary)


if __name__ == "__main__":
    unittest.main()
