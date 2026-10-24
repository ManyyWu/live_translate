const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function client(voiceOverride) {
  class Element {
    constructor() { this.children = []; this.value = ''; this.textContent = ''; this.checked = true; this.disabled = false; this.scrollTop = 0; this.scrollHeight = 1000; this.clientHeight = 200; this.handlers = {}; this.classList = {toggle() {}}; }
    append(...nodes) { for (const node of nodes) { for (const child of (node.fragment ? [...node.children] : [node])) { child.remove?.(); child.parent = this; this.children.push(child); } } }
    replaceChildren() { this.children = []; this.value = ''; }
    add(node) { this.children.push(node); if (!this.value) this.value = node.value; }
    addEventListener(name, handler) { this.handlers[name] = handler; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
  }
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const spoken = [], sources = [], timers = [];
  const voices = voiceOverride || [{voiceURI: 'remote', name: 'Cloud', lang: 'zh-CN', localService: false},
                  {voiceURI: 'local-zh', name: '本机中文', lang: 'zh-CN', localService: true},
                  {voiceURI: 'local-tw', name: '台灣中文', lang: 'zh-TW', localService: true},
                  {voiceURI: 'local-en', name: 'English', lang: 'en-US', localService: true}];
  const synth = {getVoices: () => voices, addEventListener() {}, speak: u => spoken.push(u), cancel() {}};
  const context = {document: {cookie: 'liveTranslateSpeech=' + encodeURIComponent(JSON.stringify({voice: 'local-zh', rate: 1.2, volume: .6})), getElementById: element, createElement: () => new Element(),
                   createDocumentFragment: () => Object.assign(new Element(), {fragment: true}), body: {scrollHeight: 10}},
    window: {speechSynthesis: synth, scrollTo() {}, addEventListener() {}, setTimeout(fn) {timers.push(fn); return timers.length;}, clearTimeout() {}},
    Option: class {constructor(text, value) {this.textContent = text; this.value = value;}},
    SpeechSynthesisUtterance: class {constructor(text) {this.text = text;}},
    EventSource: class {constructor() {sources.push(this);} close() {this.closed = true;}}};
  vm.runInNewContext(fs.readFileSync('live-web/app.js', 'utf8'), context);
  const message = data => sources[0].onmessage({data: JSON.stringify(data)});
  const send = (seq, items, session='test-session') => message({type: 'snapshot', session, seq, items});
  return {element, spoken, send, message, sources, document: context.document, timers, voices};
}
const item = (id, stable, ready, translation = '中文') => ({id, stable, ready, translation, text: 'English.', version: 1, playable: stable && ready});
test('grouped browser voices/cookie settings, stable-only speech and reconnect dedup', () => {
  const c = client();
  assert.equal(c.element('voice').children.length, 2);
  assert.deepEqual(c.element('voice').children.map(group => group.label), ['zh-CN', 'zh-TW']);
  // 名称的 localeCompare 排序取决于运行环境；检查条目完整性，不锁定组内第一项。
  const chineseOptions = c.element('voice').children[0].children;
  assert.deepEqual(new Set(chineseOptions.map(option => option.textContent)),
                   new Set(['★ 本地 | 本机中文', '★ 远程 | Cloud']));
  assert.deepEqual(new Set(chineseOptions.map(option => option.value)),
                   new Set(['local-zh', 'remote']));
  assert.equal(c.element('voice').value, 'local-zh');
  c.send(0, [item('old', true, true), item('new', false, true)]);
  assert.equal(c.spoken.length, 0);
  c.element('listen').onclick();
  c.send(1, [item('old', true, true), item('new', true, false, '上一版译文')]);
  assert.equal(c.spoken.length, 0);
  c.send(2, [item('old', true, true), item('new', true, true, '最新译文')]);
  assert.equal(c.spoken.length, 1);
  assert.equal(c.spoken[0].text, '最新译文');
  assert.equal(c.spoken[0].voice.localService, true);
  assert.equal(c.spoken[0].rate, 1.2);
  assert.equal(c.spoken[0].volume, .6);
  c.spoken[0].onend({type: 'end'});
  c.send(3, [item('old', true, true), item('new', true, true)]);
  assert.equal(c.spoken.length, 1);
  c.send(1, []); // Old snapshot cannot erase newer state.
  assert.equal(c.element('count').textContent, '2 句稳定 · 2 句总计');
});
test('follow scrolls only the list and preserves position when reading history', () => {
  const c = client();
  const list = c.element('items');
  c.send(0, [item('old', true, true)]);
  assert.equal(list.scrollTop, list.scrollHeight);
  list.scrollTop = 75;
  list.handlers.scroll();

  c.send(1, [item('old', true, true), item('new', false, true)]);
  assert.equal(list.scrollTop, 75);
  list.scrollTop = list.scrollHeight - list.clientHeight;
  list.handlers.scroll();
  c.send(2, [item('old', true, true), item('new', true, true)]);
  assert.equal(list.scrollTop, list.scrollHeight);
});
test('stop clears queued speech; manual history is opt-in', () => {
  const c = client();
  c.send(0, [item('old', true, true)]);
  c.element('listen').onclick();
  c.send(1, [item('old', true, true), item('one', true, true), item('two', true, true)]);
  assert.equal(c.spoken.length, 1);
  c.element('listen').onclick();
  c.spoken[0].onend({type: 'end'}); // A canceled generation must not resume its queue.
  assert.equal(c.spoken.length, 1);
  c.element('history').onclick();
  assert.equal(c.spoken.length, 2);
});

test('voice, rate and volume persist in cookies; remote browser voice can be selected', () => {
  const c = client();
  c.element('voice').value = 'remote';
  c.element('rate').value = '1.4';
  c.element('volume').value = '0.25';
  c.element('rate').handlers.input();
  const cookie = c.document.cookie.split(';')[0].split('=')[1];
  assert.deepEqual(JSON.parse(decodeURIComponent(cookie)), {voice: 'remote', rate: 1.4, volume: .25});
  c.element('test').onclick();
  assert.equal(c.spoken[0].voice.voiceURI, 'remote');
  assert.equal(c.spoken[0].rate, 1.4);
  assert.equal(c.spoken[0].volume, .25);
});

test('queued next sentence uses the rate changed during current playback', () => {
  const c = client();
  c.send(0, []);
  c.element('listen').onclick();
  c.send(1, [item('one', true, true), item('two', true, true)]);
  assert.equal(c.spoken.length, 1);
  assert.equal(c.spoken[0].rate, 1.2);
  c.element('rate').value = '1.5';
  c.element('rate').handlers.input();
  assert.equal(c.spoken[0].rate, 1.2); // Current utterance is not interrupted.
  c.spoken[0].onend({type: 'end'});
  assert.equal(c.spoken.length, 2);
  assert.equal(c.spoken[1].rate, 1.5); // Already queued text reads settings only when it starts.
});

test('keeps every Chinese voice; Android one-voice-per-locale stays one entry', () => {
  const desktop = client();
  assert.equal(desktop.element('voice').children[0].children.length, 2);
  const mobile = client([
    {voiceURI: 'cn', name: '中文 中国', lang: 'zh_CN', localService: true},
    {voiceURI: 'tw', name: '中文 台湾', lang: 'zh-TW', localService: true},
    {voiceURI: 'en', name: 'English', lang: 'en-US', localService: true},
  ]);
  assert.deepEqual(mobile.element('voice').children.map(g => g.label), ['zh-CN', 'zh-TW']);
  assert.deepEqual(mobile.element('voice').children.map(g => g.children.length), [1, 1]);
});

test('Android cmn language tags and asynchronously loaded voice list', () => {
  const c = client([]);
  assert.equal(c.element('voice').children.length, 0);
  c.voices.push({voiceURI: 'cmn-CN', name: 'Chinese', lang: 'cmn-Hans-CN', localService: true});
  c.voices.push({voiceURI: 'cmn-TW', name: 'Taiwan', lang: 'cmn-Hant-TW', localService: true});
  c.timers[0]();
  assert.deepEqual(c.element('voice').children.map(g => g.label), ['zh-CN', 'zh-TW']);
  assert.equal(c.element('voiceDebug').hidden, true);
  assert.equal(c.element('listen').disabled, false);
});

test('new server session resets sequence, history and speech dedup without refresh', () => {
  const c = client();
  c.send(100, [item('live-1', true, true)], 'session-a');
  c.element('listen').onclick();
  c.send(0, [], 'session-b');
  assert.equal(c.element('items').children.length, 0);
  c.send(1, [item('live-1', true, true, '新会话')], 'session-b');
  assert.equal(c.spoken.length, 1);
  assert.equal(c.spoken[0].text, '新会话');
});
test('incremental updates touch only changed nodes and remove obsolete drafts', () => {
  const c = client();
  c.send(0, [item('one', true, true), item('two', false, false)]);
  const firstNode = c.element('items').children[0];
  c.message({type: 'delta', session: 'test-session', base_seq: 0, seq: 2,
             upsert: [item('two', true, true, '第二句')], remove: []});
  assert.equal(c.element('items').children[0], firstNode);
  assert.equal(c.element('items').children.length, 2);
  assert.equal(c.element('items').children[1].children[1].textContent, '第二句');
  c.message({type: 'delta', session: 'test-session', base_seq: 2, seq: 3, upsert: [], remove: ['two']});
  assert.equal(c.element('items').children.length, 1);
});
test('reverse translation completion waits for ordered play permission', () => {
  const c = client(); c.send(0, []); c.element('listen').onclick();
  c.send(1, [{...item('one', true, false), playable:false}, {...item('two', true, true, '第二句'), playable:false}]);
  assert.equal(c.spoken.length, 0);
  c.message({type:'delta',session:'test-session',base_seq:1,seq:2,
             upsert:[item('one',true,true,'第一句'),item('two',true,true,'第二句')],remove:[]});
  assert.equal(c.spoken[0].text,'第一句');
  c.spoken[0].onend({type:'end'});
  assert.equal(c.spoken[1].text,'第二句');
});
test('service stop event does not permanently close the reconnecting stream', () => {
  const c = client(); c.send(0, []);
  c.message({type:'delta',session:'test-session',base_seq:0,seq:1,upsert:[],remove:[],closed:true});
  assert.equal(c.sources[0].closed, undefined);
  assert.equal(c.element('connection').textContent,'识别已停止');
});
