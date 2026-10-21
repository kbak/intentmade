"""Exercise the installed Canvas functions offline, without a factory or model."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
if Path("/runtime").is_dir():
    sys.path.insert(0, "/runtime")
import patch_canvas_activity as patch  # noqa: E402


class CanvasActivityTests(unittest.TestCase):
    def test_installed_grouping_counts_titles_and_full_details(self):
        frontend = Path(
            os.environ.get("FACTORY_TEST_CANVAS_FRONTEND", "/opt/agent-canvas/frontend")
        )
        bundles = list((frontend / "assets").glob(Path(patch.BUNDLE).stem + "-factory-*.js"))
        self.assertEqual(len(bundles), 1, "Must load the patched, cache-versioned bundle")
        bundle = bundles[0]
        self.assertFalse((frontend / "assets" / patch.BUNDLE).exists())
        self.assertIn("-factory-", (frontend / "index.html").read_text())
        for asset in (frontend / "assets").glob("*.js"):
            self.assertIn("-factory-", asset.stem)
        node = shutil.which("node") or "/acp-node/bin/node"
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const helpers = source.slice(0, source.indexOf('import'));
assert.match(helpers, /function factoryRoutineACP/);
const between = (start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing installed function: ${start}`);
  return source.slice(a, b);
};
const O = e => e.kind === 'ActionEvent';
const D = e => e.kind === 'ObservationEvent';
const k = e => e.kind === 'UserRejectObservation';
const group = new Function('M','O','A','we','Oi','gr', helpers
  + between('var Sa=', ',wa=') + ';return Ca;')(
    O, D, k, () => false, () => false, () => null);
const stats = new Function('e','t','M','me','Yi','O','A', helpers
  + between('let u=t??e,d=e.filter(M)', 'let b=_?')
  + 'return {total:h,completed:g,running:_,title:y};');
const summarize = events => stats(events, events, O,
  e => e.kind === 'AgentErrorEvent', e => ({title:e.title}), D, k);
const acp = (id, status='completed', extra={}) => ({
  kind:'ACPToolCallEvent', id, tool_call_id:id, status,
  tool_kind:'execute', title:`command ${id}`, is_error:false, ...extra,
});
const completed = acp('one');
const guardian = acp('guardian_assessment:allowed');
const running = acp('three', 'in_progress');
const events = [completed, guardian, running];
const before = JSON.stringify(events);
assert.deepEqual(group(events).map(x => x.kind), ['group']);
assert.deepEqual(group(events)[0].events, events);
assert.deepEqual(summarize(events), {
  total:3, completed:2, running:true, title:'command three'});
assert.equal(summarize([completed, guardian]).running, false);
assert.equal(summarize([completed, guardian]).completed, 2);
assert.equal(group([guardian])[0].kind, 'group');
assert.equal(JSON.stringify(events), before, 'Must not mutate persisted events');

for (const attention of [
  acp('pending', 'pending'), acp('failed', 'failed'),
  acp('error', 'completed', {is_error:true}),
  acp('missing', null), acp('future', 'unknown'),
  acp('guardian_assessment:active', 'in_progress'),
  acp('guardian_assessment:pending', 'pending'),
  acp('guardian_assessment:denied', 'failed'),
  {id:'message', kind:'MessageEvent', text:'Decision needed'},
  {id:'error-message', kind:'AgentErrorEvent'},
]) {
  const items = group([completed, attention, guardian]);
  assert.deepEqual(items.map(x => x.kind), ['group','single','group']);
  assert.equal(items[1].event, attention);
  assert.deepEqual(items.map(x => x.startIndex ?? x.index), [0,1,2]);
}
const native = {id:'native', kind:'ActionEvent', action:{kind:'TerminalAction'}};
assert.equal(group([native])[0].kind, 'single', 'Native minimum stays two');
assert.equal(group([native, {...native, id:'native2'}])[0].kind, 'group');
assert.equal(summarize([native, completed]).completed, 1);
assert.equal(summarize([native, completed]).running, true);
assert.equal(group([completed, {...native, action:{kind:'FinishAction'}}])[1].kind, 'single');

const contentFunctions = between('var V=1e3,', ',U=');
const {title, details} = new Function(helpers + contentFunctions
  + ';return {title:fn, details:xn};')();
const longTitle = 'python - <<\'PY\'\n' + 'print("context")\n'.repeat(40);
const bigOutput = 'LOG\n'.repeat(600) + 'END_OF_COMPLETE_OUTPUT';
const raw = acp('full', 'completed', {
  title:longTitle, raw_input:{command:longTitle}, raw_output:bigOutput, content:[],
});
assert.ok(title(raw).length <= 80);
assert.ok(!/[\r\n]/.test(title(raw)));
assert.ok(title(raw).endsWith('…'));
assert.ok(details(raw).includes(longTitle));
assert.ok(details(raw).includes(bigOutput));
assert.equal(title(acp('read', 'completed', {tool_kind:'read',title:'Read /file'})), '/file');
assert.equal(title(acp('mcp', 'completed', {title:'mcp.github.search_issues'})), 'mcp.github.search_issues');
const text = {...raw, tool_kind:'other', content:[{type:'content',content:{type:'text',text:bigOutput}}]};
assert.ok(details(text).includes(bigOutput));
const diff = {...raw, tool_kind:'edit', content:[{type:'diff',path:'file.txt',old_text:'old',new_text:bigOutput}]};
assert.ok(details(diff).includes('END_OF_COMPLETE_OUTPUT'));
assert.equal(raw.title, longTitle);
assert.equal(raw.raw_output, bigOutput);
// The shipped React group still starts collapsed and uses its native accessible
// toggle and opt-in child rendering; the patch changes no DOM ownership.
assert.match(source, /\[o,s\]=F.useState\(!1\)/);
assert.match(source, /"aria-expanded":o/);
assert.match(source, /o&&\(0,I.jsx\)\(`div`,\{id:c,role:`region`/);
console.log(JSON.stringify({grouping:'pass', attention:'pass', details:'pass'}));
"""
        result = subprocess.run(
            [node, "-e", script, str(bundle)], text=True, capture_output=True, check=True
        )
        # [utest~im-canvas-activity~1->req~im-canvas-activity~1]
        self.assertEqual(
            json.loads(result.stdout),
            {"grouping": "pass", "attention": "pass", "details": "pass"},
        )

    def test_unexpected_bundle_is_rejected_before_any_write(self):
        with self.assertRaisesRegex(RuntimeError, "Pinned Canvas activity bundle changed"):
            patch.patch_bundle("// different Canvas release")
