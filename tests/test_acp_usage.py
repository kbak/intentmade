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
        self.assertNotIn("factoryPromptUsage", source)
        self.assertIn('factoryFreshSession: operation === "new"', source)
        self.assertIn("await endFactoryUsage(sessionState,", source)
        with self.assertRaisesRegex(RuntimeError, "lifecycle changed"):
            patch.patch_adapter(source)
        # Exercise the published native token accumulator, not a factory copy.
        token_source = source.split("// src/TokenCount.ts\n", 1)[1].split(
            "// src/CommandUtils.ts", 1
        )[0]
        methods = []
        for name in (
            "handleTokenUsageUpdated",
            "buildQuotaMeta",
            "buildPromptUsage",
            "beginPromptTokenUsage",
            "cancelledPromptResponse",
        ):
            methods.append(re.search(r"  " + name + r"\(.*?\n  }", source, re.S).group())
        script = f"""
import assert from 'node:assert/strict';
import {{beginFactoryUsage, observeFactoryUsage, factoryUsageSnapshot, endFactoryUsage}} from {json.dumps((RUNTIME / "factory-usage.mjs").as_uri())};
{token_source}
class Adapter {{ {chr(10).join(methods)} }}
const counters = (input, output, cache=0, thought=0) => ({{totalTokens:input+output, inputTokens:input, outputTokens:output, cachedInputTokens:cache, reasoningOutputTokens:thought, cacheWriteInputTokens:0}});
const state = {{sessionId:'root', currentModelId:'test-model[high]', factoryFreshSession:true, threadHasHistory:false}};
const adapter = new Adapter(); adapter.sessionState = state;
const notify = (total, last=counters(2,7)) => adapter.handleTokenUsageUpdated({{threadId:'root', tokenUsage:{{total,last,modelContextWindow:10000}}}});
const response = () => adapter.cancelledPromptResponse(state);
const begin = () => {{ beginFactoryUsage(state); adapter.beginPromptTokenUsage(state); }};
begin();
notify(counters(100,30,20,10)); notify(counters(500,200,100,50));
assert.deepEqual(response().usage, {{totalTokens:700,inputTokens:400,cachedReadTokens:100,cachedWriteTokens:0,outputTokens:200,thoughtTokens:50}});
assert.equal(response()._meta.quota.token_count.outputTokens, 200);
let evidence; await endFactoryUsage(state, async e => evidence=e, false);
assert.equal(evidence.rawOutput.last.outputTokens, 7);
assert.equal(evidence.rawOutput.delta.outputTokens, 200);
assert.equal(evidence.rawOutput.delegated_usage, null);
// Second prompt includes a duplicate notification and two more model requests.
begin(); notify(counters(500,200,100,50)); notify(counters(650,230,120,60)); notify(counters(700,270,130,70));
assert.equal(response().usage.outputTokens,70);
assert.equal(response().usage.inputTokens,170);
await endFactoryUsage(state, async e=>evidence=e, true);
assert.equal(evidence.rawOutput.cancelled,true);
// A resumed or forked adapter with no baseline cannot count historical tokens.
const resumed={{sessionId:'resumed',currentModelId:'model',threadHasHistory:true}};
const resumedAdapter = new Adapter(); resumedAdapter.sessionState=resumed;
beginFactoryUsage(resumed);
resumedAdapter.beginPromptTokenUsage(resumed);
resumed.promptTokenUsage.observeModelOutput();
resumedAdapter.handleTokenUsageUpdated({{threadId:'resumed',tokenUsage:{{total:counters(4000,800),last:counters(100,10)}}}});
// Native fallback usage is deliberately not promoted to a verified delta.
assert.equal(resumed.promptTokenUsage.usage().outputTokens,10);
assert.equal(resumedAdapter.cancelledPromptResponse(resumed).usage,null);
assert.equal(resumedAdapter.buildQuotaMeta(resumed).quota.token_count,null);
assert.equal(factoryUsageSnapshot(resumed).reason,'missing_baseline');
await endFactoryUsage(resumed,async()=>{{}},false);
beginFactoryUsage(resumed);
resumedAdapter.beginPromptTokenUsage(resumed);
resumedAdapter.handleTokenUsageUpdated({{threadId:'resumed',tokenUsage:{{total:counters(4200,820),last:counters(200,20)}}}});
assert.equal(resumedAdapter.cancelledPromptResponse(resumed).usage.outputTokens,20);
// Counter reset or invalid fields invalidate that prompt rather than fabricate a delta.
await endFactoryUsage(state,async()=>{{}},false);
begin(); notify(counters(10,3));
assert.equal(response().usage,null); assert.equal(factoryUsageSnapshot(state).reason,'counter_reset');
await endFactoryUsage(state,async()=>{{}},false);
begin(); notify(counters(20,8));
assert.equal(response().usage.outputTokens,5);
await endFactoryUsage(state,async()=>{{}},false);
begin(); notify({{...counters(30,10),outputTokens:-1}});
assert.equal(response().usage,null);
// Commands/cancellations without notifications never reuse the previous prompt.
await endFactoryUsage(state,async()=>{{}},false); begin();
assert.equal(response().usage,null);
// Native cache-write accounting excludes both read and write caches from fresh input.
const native = new PromptTokenUsage(ZERO_TOKEN_COUNT);
native.observe(toTokenCount({{...counters(100,20,30),cacheWriteInputTokens:10}}), toTokenCount(counters(100,20,30)));
assert.deepEqual(toPromptUsage(native.usage()), {{totalTokens:120,inputTokens:60,cachedReadTokens:30,cachedWriteTokens:10,outputTokens:20,thoughtTokens:0}});
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
