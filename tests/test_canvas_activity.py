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
        bundles = []
        for name in patch.BUNDLES:
            matches = list((frontend / "assets").glob(Path(name).stem + "-factory-*.js"))
            self.assertEqual(len(matches), 1, "Must load the patched, cache-versioned bundle")
            bundles.append(matches[0])
            self.assertFalse((frontend / "assets" / name).exists())
        self.assertIn("-factory-", (frontend / "index.html").read_text())
        for asset in (frontend / "assets").glob("*.js"):
            self.assertIn("-factory-", asset.stem)
        node = shutil.which("node") or "/acp-node/bin/node"
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const contentSource = fs.readFileSync(process.argv[2], 'utf8');
const helpers = source.slice(0, source.indexOf('import'));
assert.match(helpers, /function factoryRoutineACP/);
const between = (start, end, text=source) => {
  const a = text.indexOf(start), b = text.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing installed function: ${start}`);
  return text.slice(a, b);
};
const O = e => e.kind === 'ActionEvent';
const D = e => e.kind === 'ObservationEvent';
const k = e => e.kind === 'UserRejectObservation';
const isACP = e => e.kind === 'ACPToolCallEvent';
const group = new Function('P','F','I','R','N','me','Ze','fe','M','Q','H','tt','$e', helpers
  + between('var vr=', ',br=') + ';return yr;')(
    O, D, k, isACP, e => e.kind === 'HookExecutionEvent',
    () => false, () => false, e => e.kind === 'MessageEvent',
    e => e.kind === 'StreamingDeltaEvent', () => '', () => '', () => null,
    events => new Map(events.filter(O).map(event => [event.id, event])));
const running = new Function('e','S','te','g','ne',
  between('let t=new Set(S.filter(te)', ';w=n?', contentSource) + ';return n;');
const live = events => running(events, events, e => e.kind === 'AgentErrorEvent', O, isACP);
const counts = new Function(helpers + ';return factoryActivityCounts;')();
const acp = (id, status='completed', extra={}) => ({
  kind:'ACPToolCallEvent', id, tool_call_id:id, status,
  tool_kind:'execute', title:`command ${id}`, is_error:false, ...extra,
});
const completed = acp('one');
const guardian = acp('guardian_assessment:allowed');
const active = acp('three', 'in_progress');
const events = [completed, guardian, active];
const before = JSON.stringify(events);
assert.deepEqual(group(events).map(x => x.kind), ['group']);
assert.deepEqual(group(events)[0].events, events);
assert.equal(live(events), active);
assert.equal(live([completed, guardian]), undefined);
assert.deepEqual(counts(events), {total:3, completed:2, running:1});
assert.deepEqual(counts([completed, guardian]), {total:2, completed:2, running:0});
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
assert.equal(live([native, completed]), native);
assert.deepEqual(counts([native, completed]), {total:2, completed:1, running:1});
assert.equal(counts([native], [native, {kind:'ObservationEvent',action_id:native.id}]).running, 0);
assert.equal(live([native, {kind:'AgentErrorEvent',tool_call_id:native.tool_call_id}]), undefined);
assert.equal(group([completed, {...native, action:{kind:'FinishAction'}}])[1].kind, 'single');

const contentFunctions = between('var L=1e3,', ',U=', contentSource);
const {title, details} = new Function(helpers + contentFunctions
  + ';return {title:ft, details:bt};')();
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
assert.match(contentSource, /\[m,ee\]=A.useState\(!1\),h=c\?\?m/);
assert.match(contentSource, /"aria-expanded":h/);
assert.match(contentSource, /h&&\(0,O.jsx\)\(`div`,\{id:b,role:`region`/);
assert.ok(contentSource.includes('factoryCounts=factoryActivityCounts(e,S)'));
assert.ok(contentSource.includes('(${factoryCounts.completed}/${factoryCounts.total})'));
console.log(JSON.stringify({grouping:'pass', attention:'pass', details:'pass'}));
"""
        result = subprocess.run(
            [node, "-e", script, *map(str, bundles)], text=True, capture_output=True, check=True
        )
        # [utest~im-canvas-activity~1->req~im-canvas-activity~1]
        self.assertEqual(
            json.loads(result.stdout),
            {"grouping": "pass", "attention": "pass", "details": "pass"},
        )

    def test_unexpected_bundle_is_rejected_before_any_write(self):
        for bundle in patch.BUNDLES:
            with self.subTest(bundle=bundle):
                with self.assertRaisesRegex(RuntimeError, "Pinned Canvas activity bundle changed"):
                    patch.patch_bundle("// different Canvas release", bundle)
