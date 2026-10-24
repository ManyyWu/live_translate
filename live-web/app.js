/* 使用浏览器提供的语音，不依赖 Edge TTS 或扩展权限。 */
(() => {
  const $ = id => document.getElementById(id);
  const synth = window.speechSynthesis;
  const nodes = new Map();
  const seen = new Set();
  const records = new Map(), stableIds = new Set();
  let session = null, highlighted = null;
  let voices = [], initialized = false, seq = -1, enabled = false;
  let queue = [], current = null, generation = 0;
  let voiceRetry = null, voiceAttempts = 0;
  let settings, following = true;
  try {
    const cookie = document.cookie.split('; ').find(value => value.startsWith('liveTranslateSpeech='));
    settings = cookie ? JSON.parse(decodeURIComponent(cookie.slice('liveTranslateSpeech='.length))) : {};
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) settings = {};
  } catch { settings = {}; }
  $('rate').value = Number.isFinite(settings.rate) ? Math.max(.5, Math.min(1.5, settings.rate)) : 1;
  $('volume').value = Number.isFinite(settings.volume) ? Math.max(0, Math.min(1, settings.volume)) : 1;
  const status = text => { $('speechStatus').textContent = text; $('speechStatus').hidden = !/错误|没有可用|没有匹配|请选择/.test(text); };
  // ready 仅表示当前英文版本的译文就绪，暂留的旧译文不能进入播放队列。
  const stable = item => item.stable && item.ready && !!item.translation && !item.error;
  /** 将语音、倍速和音量保存到 Cookie；不保存播放队列或临时识别内容。 */
  function save() {
    settings = {voice: $('voice').value || settings.voice || '', rate: Number($('rate').value), volume: Number($('volume').value)};
    try { document.cookie = `liveTranslateSpeech=${encodeURIComponent(JSON.stringify(settings))}; Max-Age=31536000; Path=/; SameSite=Lax`; } catch {}
    $('rateValue').textContent = settings.rate + '×';
    $('volumeValue').textContent = Math.round(settings.volume * 100) + '%';
  }
  /** 重新枚举中文声音、按地区分组，并处理移动端延迟加载和空列表。 */
  function listVoices() {
    const selected = $('voice').value || settings.voice;
    // 兼容桌面和 Android 的语言标记；同一语言的不同声音不能合并去重。
    const groupLanguage = voice => {
      const lang = (voice.lang || '').trim().replace(/_/g, '-').toLowerCase();
      const parts = lang.split('-');
      if (['zh', 'cmn', 'zho', 'chi'].includes(parts[0])) {
        if (parts.includes('tw') || parts.includes('twn') || parts.includes('hant')) return 'zh-TW';
        if (parts.includes('hk') || parts.includes('mo')) return '';
        return 'zh-CN';
      }
      // 部分 Android 引擎的语言标记不可用，尝试从名称或 URI 识别地区。
      const description = `${voice.name || ''} ${voice.voiceURI || ''}`;
      if (/(?:zh|cmn)[-_](?:hant[-_])?tw|台[湾灣]|chinese.*taiwan/i.test(description)) return 'zh-TW';
      if (/(?:zh|cmn)[-_](?:hans[-_])?cn|普通话|普通話|中文.*中国|中文.*中國|chinese.*china|mandarin/i.test(description)) return 'zh-CN';
      return '';
    };
    const available = synth ? synth.getVoices() : [];
    voices = available.filter(voice => groupLanguage(voice));
    $('voiceDebug').hidden = voices.length > 0;
    $('voiceDetails').textContent = `浏览器返回 ${available.length} 个声音，匹配中文 ${voices.length} 个。\n` +
      available.map(v => `${v.lang || '(无语言标记)'} | ${v.name} | ${v.voiceURI}`).join('\n');
    // 手机 Chrome 首次可能返回空列表，且未必触发 voiceschanged，因此有限次重试。
    if (!voices.length && voiceAttempts < 12 && voiceRetry === null) {
      voiceAttempts++;
      voiceRetry = window.setTimeout(() => { voiceRetry = null; listVoices(); }, 500);
    } else if (voices.length && voiceRetry !== null) {
      window.clearTimeout(voiceRetry); voiceRetry = null;
    }
    voices.sort((a, b) => Number(groupLanguage(a) === 'zh-TW') - Number(groupLanguage(b) === 'zh-TW') || a.name.localeCompare(b.name));
    $('voice').replaceChildren();
    const groups = new Map();
    for (const voice of voices) {
      const language = groupLanguage(voice);
      if (!groups.has(language)) {
        const group = document.createElement('optgroup'); group.label = language;
        groups.set(language, group); $('voice').append(group);
      }
      groups.get(language).append(new Option(`${language === 'zh-CN' ? '★ ' : ''}${voice.localService ? '本地 | ' : '远程 | '}${voice.name}`, voice.voiceURI));
    }
    if (voices.some(v => v.voiceURI === selected)) $('voice').value = selected;
    else if (voices.length) $('voice').value = voices[0].voiceURI;
    for (const id of ['listen', 'history', 'test']) $(id).disabled = !voices.length;
    if (!voices.length) status(available.length ? '没有匹配的中文语音，请展开语音诊断。' : '没有可用的语音，正在等待系统加载；可展开语音诊断。');
    else if (/^没有(?:可用|匹配)/.test($('speechStatus').textContent)) status('只朗读稳定译文。连接后不自动播放历史。');
    save();
  }
  /** 只给正在朗读的句子加高亮，不改变文本和播放顺序。 */
  function highlight() {
    if (highlighted !== null) nodes.get(highlighted)?.classList.toggle('speaking', false);
    highlighted = current?.id ?? null;
    if (highlighted !== null) nodes.get(highlighted)?.classList.toggle('speaking', true);
  }
  /** 空闲时取队首句子；开始播放时才读取最新语音、倍速和音量。 */
  function pump() {
    if (current || !queue.length || !synth) return;
    const voice = voices.find(v => v.voiceURI === $('voice').value);
    if (!voice) { status('请选择可用的本机语音。'); return; }
    const item = queue.shift(), token = generation;
    const utterance = new SpeechSynthesisUtterance(item.translation);
    utterance.voice = voice; utterance.lang = voice.lang;
    utterance.rate = Number($('rate').value); utterance.volume = Number($('volume').value); utterance.pitch = 1;
    current = {id: item.id, utterance}; // 持有强引用直到播放结束，避免话语对象被提前回收。
    highlight(); status(`正在朗读 · 队列 ${queue.length} 句`);
    const finish = event => {
      // stop() 之后，旧话语的 end/error 回调不可再推进已清空的队列。
      if (token !== generation) return;
      current = null; highlight();
      if (event.type === 'error') status(`语音错误：${event.error}`);
      else status(queue.length ? `等待朗读 ${queue.length} 句` : enabled ? '等待新的稳定译文…' : '播放完成');
      pump();
    };
    utterance.onend = finish; utterance.onerror = finish;
    synth.speak(utterance);
  }
  /** 清空队列并取消当前朗读；递增代号使迟到的结束回调失效。 */
  function stop() {
    generation++; queue = []; current = null;
    if (synth) synth.cancel();
    highlight();
  }
  /** 手动朗读单句，仅允许稳定且有当前版本译文的条目。 */
  function playOne(item) {
    if (!item || !stable(item)) return;
    stop(); queue.push(item); pump();
  }
  /** 首次全量，后续只更新增量条目；新会话重置序号及播放去重。 */
  function render(data) {
    const incomingSession = data.session;
    if (incomingSession !== session) {
      stop(); seen.clear(); records.clear(); stableIds.clear(); nodes.clear();
      $('items').replaceChildren();
      session = incomingSession; seq = -1; initialized = false;
    }
    if (data.seq <= seq) return;
    const full = data.type !== 'delta';
    if (!full && data.base_seq !== seq) {
      // 正常 SSE 会合并遗漏版本；若基线仍不一致，主动取全量恢复。
      const recoverySession = session;
      fetch('/api/state').then(response => response.json()).then(snapshot => {
        if (session === recoverySession) render(snapshot);
      })
        .catch(error => status(`同步错误：${error.message}`));
      return;
    }
    const updates = full ? data.items : data.upsert;
    const snapshotIds = full ? new Set(data.items.map(item => item.id)) : null;
    const removed = full ? [...records.keys()].filter(id => !snapshotIds.has(id)) : data.remove;
    seq = data.seq;
    const follow = following, scrollTop = $('items').scrollTop;
    for (const id of removed) {
      nodes.get(id)?.remove(); nodes.delete(id); records.delete(id); stableIds.delete(id);
    }
    const fragment = document.createDocumentFragment();
    for (const item of updates) {
      records.set(item.id, item);
      if (stable(item)) stableIds.add(item.id); else stableIds.delete(item.id);
      let node = nodes.get(item.id);
      if (!node) {
        node = document.createElement('article'); node.className = 'item';
        const en = document.createElement('p'); en.className = 'en';
        const zh = document.createElement('p'); zh.className = 'zh';
        const meta = document.createElement('div'); meta.className = 'meta';
        const label = document.createElement('span'), button = document.createElement('button');
        button.textContent = '朗读'; button.onclick = () => playOne(records.get(item.id));
        meta.append(label, button); node.append(en, zh, meta); nodes.set(item.id, node);
        fragment.append(node); // 历史节点不反复移出 DOM，避免滚动跳动和全量重排。
      }
      node.classList.toggle('draft', !stable(item));
      node.children[0].textContent = item.text;
      node.children[1].textContent = item.translation || (item.error ? '翻译失败' : '翻译中…');
      node.children[2].children[0].textContent = stable(item) ? '✓' : '';
      node.children[2].children[0].title = stable(item) ? '已稳定' : '';
      node.children[2].children[1].disabled = !stable(item);
      // 后一句即使先翻译完成，也须等服务端按原文顺序开放播放。
      if (stable(item) && item.playable && !seen.has(item.id)) {
        seen.add(item.id);
        if (initialized && enabled) queue.push({...item});
      }
    }
    initialized = true;
    $('items').append(fragment); $('empty').hidden = records.size > 0;
    $('count').textContent = `${stableIds.size} 句稳定 · ${records.size} 句总计`;
    highlight(); pump();
    $('items').scrollTop = follow ? $('items').scrollHeight : scrollTop;
    // 不永久关闭 EventSource；服务退出后仍允许自动连接新会话。
    if (data.closed) $('connection').textContent = '识别已停止';
  }
  $('listen').onclick = () => {
    enabled = !enabled;
    $('listen').textContent = enabled ? '停止实时朗读' : '开启实时朗读';
    if (!enabled) { stop(); status('实时朗读已停止'); }
    else { for (const item of records.values()) if (stable(item) && item.playable) seen.add(item.id); status('等待新的稳定译文…'); pump(); }
  };
  $('history').onclick = () => { stop(); queue = [...records.values()].filter(stable).map(item => ({...item})); pump(); };
  $('test').onclick = () => { stop(); queue.push({id: 'test', translation: '这是本机中文语音试听。'}); pump(); };
  for (const id of ['voice', 'rate', 'volume']) $(id).addEventListener('input', save);
  $('items').addEventListener('scroll', () => {
    const list = $('items');
    // 到达底部恢复跟随，离开底部立即停止；不能由新消息强行拉回底部。
    following = list.scrollHeight - list.scrollTop - list.clientHeight <= 2;
  });
  $('reloadVoices').onclick = () => { voiceAttempts = 0; listVoices(); };
  window.addEventListener('focus', listVoices);
  save(); listVoices();
  if (synth) synth.addEventListener('voiceschanged', listVoices);
  const source = new EventSource('/api/events');
  source.onopen = () => { $('connection').textContent = '已连接'; };
  source.onerror = () => { $('connection').textContent = '连接断开 · 正在重连'; };
  source.onmessage = event => { try { render(JSON.parse(event.data)); } catch (error) { status(`同步错误：${error.message}`); } };
  window.addEventListener('pagehide', () => { source.close(); stop(); });
})();
