"""Exercise real pinned adapter conversion/response functions without model calls."""

import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "runtime"
if not RUNTIME.is_dir():
    RUNTIME = Path("/opt/factory")


class ACPUsageTests(unittest.TestCase):
    def test_pinned_adapter_reports_prompt_deltas_and_preserves_unknowns(self):
        spec = importlib.util.spec_from_file_location("usage_patch", RUNTIME / "patch_acp_usage.py")
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        # The built image is already patched; source-overlay regression runs are not.
        if "beginFactoryUsage(sessionState);" not in source:
            source = patch.patch_adapter(source)
        subprocess.run(
            ["/acp-node/bin/node", "--input-type=module", "--check"],
            input=source,
            text=True,
            check=True,
        )
        self.assertEqual(source.count("this.buildPromptUsage(factoryPromptUsage(sessionState))"), 4)
        self.assertIn('factoryFreshSession: operation === "new"', source)
        self.assertIn("await endFactoryUsage(sessionState,", source)
        with self.assertRaisesRegex(RuntimeError, "lifecycle changed"):
            patch.patch_adapter(source)
        functions = []
        for name in ("toTokenCount", "toPromptUsage"):
            functions.append(re.search(r"function " + name + r"\(.*?\n}", source, re.S).group())
        methods = []
        for name in (
            "handleTokenUsageUpdated",
            "buildQuotaMeta",
            "buildPromptUsage",
            "cancelledPromptResponse",
        ):
            methods.append(re.search(r"  " + name + r"\(.*?\n  }", source, re.S).group())
        script = f"""
import assert from 'node:assert/strict';
import {{beginFactoryUsage, observeFactoryUsage, factoryPromptUsage, factoryUsageSnapshot, endFactoryUsage}} from {json.dumps((RUNTIME / "factory-usage.mjs").as_uri())};
{chr(10).join(functions)}
class Adapter {{ {chr(10).join(methods)} }}
const counters = (input, output, cache=0, thought=0) => ({{totalTokens:input+output, inputTokens:input, outputTokens:output, cachedInputTokens:cache, reasoningOutputTokens:thought}});
const state = {{sessionId:'root', currentModelId:'test-model[high]', factoryFreshSession:true}};
const adapter = new Adapter(); adapter.sessionState = state;
const notify = (total, last=counters(2,7)) => adapter.handleTokenUsageUpdated({{threadId:'root', tokenUsage:{{total,last,modelContextWindow:10000}}}});
const response = () => adapter.cancelledPromptResponse(state);
beginFactoryUsage(state);
notify(counters(100,30,20,10)); notify(counters(500,200,100,50));
assert.deepEqual(response().usage, {{totalTokens:700,inputTokens:400,cachedReadTokens:100,outputTokens:200,thoughtTokens:50}});
assert.equal(response()._meta.quota.token_count.outputTokens, 200);
let evidence; await endFactoryUsage(state, async e => evidence=e, false);
assert.equal(evidence.rawOutput.last.outputTokens, 7);
assert.equal(evidence.rawOutput.delta.outputTokens, 200);
assert.equal(evidence.rawOutput.delegated_usage, null);
// Second prompt includes a duplicate notification and two more model requests.
beginFactoryUsage(state); notify(counters(500,200,100,50)); notify(counters(650,230,120,60)); notify(counters(700,270,130,70));
assert.equal(response().usage.outputTokens,70);
assert.equal(response().usage.inputTokens,170);
await endFactoryUsage(state, async e=>evidence=e, true);
assert.equal(evidence.rawOutput.cancelled,true);
// A resumed or forked adapter with no baseline cannot count historical tokens.
const resumed={{sessionId:'resumed',currentModelId:'model'}};
beginFactoryUsage(resumed);
observeFactoryUsage(resumed,{{threadId:'resumed',tokenUsage:{{total:counters(4000,800),last:counters(100,10)}}}});
assert.equal(factoryPromptUsage(resumed),null);
assert.equal(factoryUsageSnapshot(resumed).reason,'missing_baseline');
await endFactoryUsage(resumed,async()=>{{}},false);
beginFactoryUsage(resumed);
observeFactoryUsage(resumed,{{threadId:'resumed',tokenUsage:{{total:counters(4200,820),last:counters(200,20)}}}});
assert.equal(factoryPromptUsage(resumed).outputTokens,20);
// Counter reset or invalid fields invalidate that prompt rather than fabricate a delta.
await endFactoryUsage(state,async()=>{{}},false);
beginFactoryUsage(state); notify(counters(10,3));
assert.equal(response().usage,null); assert.equal(factoryUsageSnapshot(state).reason,'counter_reset');
await endFactoryUsage(state,async()=>{{}},false);
beginFactoryUsage(state); notify(counters(20,8));
assert.equal(response().usage.outputTokens,5);
await endFactoryUsage(state,async()=>{{}},false);
beginFactoryUsage(state); notify({{...counters(30,10),outputTokens:-1}});
assert.equal(response().usage,null);
// Commands/cancellations without notifications never reuse the previous prompt.
await endFactoryUsage(state,async()=>{{}},false); beginFactoryUsage(state);
assert.equal(response().usage,null);
// Child notifications are not blindly added or attributed to the parent.
const fresh={{sessionId:'a',factoryFreshSession:true}}; beginFactoryUsage(fresh);
observeFactoryUsage(fresh,{{threadId:'b',tokenUsage:{{total:counters(999,999)}}}});
assert.equal(factoryUsageSnapshot(fresh).notifications,0);
console.log('multi-request, repeat, resumed, cancelled, reset, missing, child counters passed');
"""
        output = subprocess.check_output(
            ["/acp-node/bin/node", "--input-type=module", "-e", script], text=True
        )
        self.assertIn("counters passed", output)


if __name__ == "__main__":
    unittest.main()
