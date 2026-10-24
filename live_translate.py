#!/usr/bin/env python3
"""实时识别、异步批量翻译与网页同步；仅依赖 Python 标准库。"""

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import re
import argparse
import codecs
import os
from pathlib import Path
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import unicodedata
import threading
import uuid
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class WebState:
    """保存本次会话状态及有限增量记录；主线程发布，HTTP 线程读取。"""
    def __init__(self):
        """每次服务启动生成独立会话 ID，不读取往次文件。"""
        self.condition = threading.Condition()
        self.closed = False
        self.session = uuid.uuid4().hex
        self.seq = 0
        self.items = OrderedDict()
        self.draft_ids = set()
        self.changes = deque(maxlen=256)

    @staticmethod
    def item(entry, stable, playable=False):
        """稳定、译文就绪、允许顺序播放分别标记，避免混淆三个边界。"""
        return dict(id=f'live-{entry.id}', order=entry.id, text=entry.text,
                    translation=entry.translation or entry.previous_translation,
                    stable=stable, ready=bool(entry.translation), version=entry.version,
                    error=bool(entry.error), playable=playable)

    def publish(self, committed, draft):
        """仅比较当前临时区和新固定项，历史区无需逐条重新遍历。"""
        with self.condition:
            committed_ids = {item['id'] for item in committed}
            next_draft = {item['id'] for item in draft}
            removed = self.draft_ids - committed_ids - next_draft
            for item_id in removed:
                self.items.pop(item_id, None)
            upsert = []
            for item in committed + draft:
                if self.items.get(item['id']) != item:
                    self.items[item['id']] = item
                    upsert.append(item)
            self.draft_ids = next_draft
            if not upsert and not removed:
                return
            self.seq += 1
            self.changes.append(dict(seq=self.seq, upsert=upsert, remove=list(removed)))
            self.condition.notify_all()

    def snapshot(self):
        """返回全量快照；用于首次连接、重新连接或增量记录已过期时。"""
        with self.condition:
            return dict(type='snapshot', session=self.session, seq=self.seq,
                        items=list(self.items.values()), closed=self.closed)

    def since(self, seq):
        """合并客户端遗漏的增量；记录不足时退回全量，不让慢客户端漏句。"""
        with self.condition:
            if seq < 0 or seq > self.seq or (self.changes and seq < self.changes[0]['seq'] - 1):
                return self.snapshot()
            changed = OrderedDict()
            removed = set()
            for change in self.changes:
                if change['seq'] <= seq:
                    continue
                for item_id in change['remove']:
                    changed.pop(item_id, None)
                    removed.add(item_id)
                for item in change['upsert']:
                    removed.discard(item['id'])
                    changed[item['id']] = item
            # 合并多次乱序翻译更新后，字典插入顺序不再等同于原文顺序。
            ordered = sorted(changed.values(), key=lambda item: item.get('order', 0))
            return dict(type='delta', session=self.session, base_seq=seq, seq=self.seq,
                        upsert=ordered, remove=list(removed), closed=self.closed)

    def close(self):
        """发布结束事件，唤醒等待中的 SSE；浏览器可继续等待下一次服务启动。"""
        with self.condition:
            self.closed = True
            self.seq += 1
            self.changes.append(dict(seq=self.seq, upsert=[], remove=[]))
            self.condition.notify_all()


def start_web(state, host, port):
    """启动标准库 HTTP 服务，提供固定静态资源、状态快照和 SSE 推送。"""
    assets = Path(__file__).resolve().parent / 'live-web'

    class Handler(BaseHTTPRequestHandler):
        """仅处理允许的网页路由，不暴露任意文件或命令执行。"""
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            """关闭默认 HTTP 控制台日志，避免破坏终端原位刷新。"""
            pass  # HTTP 日志不能打断终端重绘。

        def do_GET(self):
            """处理 GET；SSE 首次全量、后续更新及心跳均在此发送。"""
            route = urlsplit(self.path).path
            if route == '/api/events':
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                # -1 强制首次发送全量快照；每个客户端独立维护已发送序号。
                last = -1
                try:
                    while True:
                        with state.condition:
                            state.condition.wait_for(lambda: state.seq != last or state.closed, timeout=10)
                            data = state.since(last)
                        if data['seq'] != last or data['closed']:
                            last = data['seq']
                            payload = f'id: {last}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
                        else:
                            payload = ': heartbeat\n\n'
                        # 网络写入必须放在状态锁之外，慢客户端不能阻塞识别主线程。
                        self.wfile.write(payload.encode('utf-8'))
                        self.wfile.flush()
                        if data['closed']:
                            break
                # 手机切后台或刷新可直接断开；结束本连接即可，不影响其他客户端。
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                return
            if route == '/api/state':
                body = json.dumps(state.snapshot(), ensure_ascii=False).encode('utf-8')
                mime = 'application/json; charset=utf-8'
            # 只映射固定文件名，不能把 URL 路径直接拼接到磁盘路径。
            elif route in ('/', '/app.js', '/style.css', '/live.svg'):
                name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/live.svg': 'live.svg'}[route]
                try:
                    body = (assets / name).read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                mime = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css', '/live.svg': 'image/svg+xml'}[route] + '; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name='live-http').start()
    return server


def google_translate(texts):
    # 重复 q 参数保留条目边界，不依赖可能被翻译改写的分隔符。
    """批量调用 Google 网页接口；校验条目数量和非空译文后按原顺序返回。"""
    body = urlencode([('q', text) for text in texts]).encode()
    request = Request(
        'https://translate.googleapis.com/translate_a/t?client=gtx&dt=t&sl=en&tl=zh-CN',
        data=body, headers={'Content-Type': 'application/x-www-form-urlencoded',
                            'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=8) as response:
        data = json.load(response)
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError('translation response count mismatch')
    result = []
    for entry in data:
        value = entry[0] if isinstance(entry, list) and entry else entry
        if not isinstance(value, str) or not value.strip():
            raise ValueError('translation response contains an empty/invalid entry')
        result.append(value.strip())
    return result


@dataclass
class Entry:
    """记录一句话的身份、文本版本、译文及稳定状态。"""
    id: int
    text: str
    version: int = 1
    translation: str = ''
    previous_translation: str = ''
    error: str = ''
    applied_batch: int = 0
    stable: bool = False


class Translator:
    """管理翻译合批、并发、缓存；网络线程不直接更新界面。"""
    def __init__(self, on_result, *, transport=google_translate, workers=4,
                 batch_wait=0.05, batch_size=16, batch_chars=8000, event=None):
        """初始化合批限制、线程池和待发送/进行中请求集合。"""
        self.on_result = on_result
        self.transport = transport
        self.workers = workers
        self.batch_wait = batch_wait
        self.batch_size = batch_size
        self.batch_chars = batch_chars
        self.event = event or (lambda **kwargs: None)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='translate')
        self.pending = OrderedDict()
        self.inflight = {}
        self.cache = OrderedDict()
        self.batch_id = 0
        self.queued_at = None

    def enqueue(self, entry):
        """优先读取缓存，否则用同一句子的最新版本覆盖尚未发送的旧版本。"""
        self.pending.pop(entry.id, None)
        if entry.text in self.cache:
            entry.translation = self.cache[entry.text]
            return
        if not self.pending:
            self.queued_at = time.monotonic()
        # 尚未发送的同一句子只保留最新版，避免为旧文本浪费请求。
        self.pending[entry.id] = (entry.id, entry.version, entry.text)

    def discard(self, entry_id):
        """删除待发送条目；已发送请求由响应端版本检查丢弃。"""
        self.pending.pop(entry_id, None)

    def tick(self, force=False):
        # 主线程解包并应用响应，避免网络线程同时修改句子和界面。
        """收取已完成响应，再在并发上限内发送合批请求；不阻塞识别主循环。"""
        for future, batch in list(self.inflight.items()):
            if not future.done():
                continue
            del self.inflight[future]
            batch_id, sent_at, items = batch
            try:
                translated = future.result()
                if len(translated) != len(items) or any(not isinstance(t, str) or not t.strip() for t in translated):
                    raise ValueError('translation response count/type mismatch')
                error = ''
            except Exception as exc:
                translated = [''] * len(items)
                error = f'{type(exc).__name__}: {exc}'
            for (entry_id, version, source), target in zip(items, translated):
                if not error:
                    self.cache[source] = target
                    self.cache.move_to_end(source)
                    while len(self.cache) > 512:
                        self.cache.popitem(last=False)
                self.on_result(entry_id, version, batch_id, target, error)
            self.event(type='batch_received', batch_id=batch_id, sent_at=sent_at, error=error)

        if not self.pending:
            return
        if not force and time.monotonic() - self.queued_at < self.batch_wait:
            return
        while self.pending and len(self.inflight) < self.workers:
            items = []
            chars = 0
            while self.pending and len(items) < self.batch_size:
                key, item = next(iter(self.pending.items()))
                if items and chars + len(item[2]) > self.batch_chars:
                    break
                del self.pending[key]
                items.append(item)
                chars += len(item[2])
            self.batch_id += 1
            sent_at = time.time()
            future = self.executor.submit(self.transport, [item[2] for item in items])
            self.inflight[future] = (self.batch_id, sent_at, items)
            self.event(type='batch_sent', batch_id=self.batch_id, sent_at=sent_at,
                       entries=[{'id': i, 'version': v} for i, v, _ in items])

    def close(self):
        """等待运行中的请求结束，关闭网络线程池。"""
        self.executor.shutdown(wait=True, cancel_futures=True)


LINK_WORDS = {'a', 'an', 'the', 'to', 'of', 'with', 'for', 'from', 'by',
              'and', 'or', 'but', 'because', 'although'}
AUXILIARIES = {'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
               'have', 'has', 'had', 'will', 'would', 'can', 'could', 'should', 'must'}


def incomplete(text):
    """保守判断明显半句；不能据此保证语法或识别内容正确。"""
    if not text.endswith('.'):
        return False
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.lower())
    if not words:
        return False
    # "like that." and "it hasn't been." can be complete thoughts.
    if words[-1] == 'been' or (len(words) > 1 and words[-2] in {"haven't", "hasn't", "hadn't"}):
        return False
    return words[-1] in LINK_WORDS or (len(words) >= 3 and words[-1] in AUXILIARIES)


def join_fragment(carry, text):
    """拼接跨块半句，仅消除边界处重复的同一个助动词。"""
    if not carry:
        return text
    if not text:
        return carry
    # Only remove an exact repeated auxiliary at a fragment boundary, e.g.
    # "The roadster was" + "was announced ...". Ordinary repetitions are retained.
    last = re.search(r'\b([A-Za-z]+)$', carry)
    first = re.match(r'([A-Za-z]+)\b\s*', text)
    if last and first and last[1].lower() == first[1].lower() and last[1].lower() in AUXILIARIES:
        text = text[first.end():]
    return ' '.join(filter(None, [carry, text]))


class BilingualTranscript:
    """协调文本修订、翻译版本、终端展示、稳定文件与网页同步。"""
    def __init__(self, terminal, normalize, sentences, event_file=None, output_file=None, web_state=None, **translation_options):
        """初始化当前块、待固定句子和翻译调度器。"""
        self.terminal = terminal
        self.normalize = normalize
        self.sentences = sentences
        self.event_file = event_file
        self.output_file = output_file
        self.web_state = web_state
        self.entries = {}
        self.active = []
        self.finalized = []
        self.next_id = 0
        self.carry = ''
        self.changed = False
        self.translator = Translator(self.result, event=self.event, **translation_options)

    def event(self, **event):
        """将事件逐行写入可选 JSONL 日志并立即刷新。"""
        if self.event_file:
            self.event_file.write(json.dumps({'time': time.time(), **event}, ensure_ascii=False) + '\n')
            self.event_file.flush()

    def accept(self, text, final, block, revision):
        """接收识别快照，重组句子、维护版本并安排临时文本翻译。"""
        combined = self.normalize(join_fragment(self.carry, text))
        complete, tail = self.sentences(combined)
        texts = complete + ([tail] if tail else [])
        # 可变识别块按句子位置复用 ID；分句变化会增加版本，删除位置立即失效。
        # 已发出的请求不能取消其返回，但返回时会检查 ID 和版本。
        for index, source in enumerate(texts):
            if index < len(self.active):
                entry = self.active[index]
                if entry.text == source:
                    continue
                entry.version += 1
                entry.text = source
                # 显示层保留上一版译文；逻辑上仍须等待当前版本翻译完成，
                # 不能用旧译文提前固定新文本。
                entry.previous_translation = entry.translation or entry.previous_translation
                entry.translation = entry.error = ''
            else:
                self.next_id += 1
                entry = Entry(self.next_id, source)
                self.entries[entry.id] = entry
                self.active.append(entry)
            self.translator.enqueue(entry)
        for entry in self.active[len(texts):]:
            self.entries.pop(entry.id, None)
            self.translator.discard(entry.id)
        self.active = self.active[:len(texts)]
        self.event(type='block_end' if final else 'replace', block=block, revision=revision, text=combined)
        if final:
            for entry in self.active[:len(complete)]:
                entry.stable = True
            self.finalized.extend(self.active[:len(complete)])
            self.active = self.active[len(complete):]
            self.carry = tail
        self.changed = True
        self.render()

    def result(self, entry_id, version, batch_id, target, error):
        """逐条校验版本和包序号，只接受仍适用于当前句子的响应。"""
        entry = self.entries.get(entry_id)
        # 按条目检查文本版本和包序号，不能整包直接覆盖。
        # 同一迟到响应中，过期条目丢弃，其他仍有效的条目继续使用。
        if entry is None or entry.version != version or batch_id <= entry.applied_batch:
            self.event(type='translation_discarded', entry_id=entry_id, version=version, batch_id=batch_id)
            return
        entry.applied_batch = batch_id
        entry.translation, entry.error = target, error
        self.changed = True
        self.event(type='translation', entry_id=entry_id, version=version, batch_id=batch_id,
                   text=entry.text, translation=target, error=error)

    @staticmethod
    def lines(entry):
        """生成英文/中文两行；新译文未返回时继续显示上一版译文。"""
        return [entry.text, entry.translation or entry.previous_translation or
                ('[翻译失败]' if entry.error else '[翻译中…]')]

    def render(self):
        """按原文顺序固定就绪条目，并刷新终端和网页中的临时内容。"""
        if not self.changed:
            return
        committed = []
        web_committed = []
        # 即使后面的请求先完成，固定输出仍须保持原文顺序。
        while self.finalized and (self.finalized[0].translation or self.finalized[0].error):
            entry = self.finalized.pop(0)
            committed.extend(self.lines(entry))
            web_committed.append(WebState.item(entry, entry.stable,
                                                playable=entry.stable and bool(entry.translation) and not entry.error))
            # 文件只接收结束块中的完整句子及其当前版本译文。
            # 显示层暂留的旧译文、失败占位符均不能作为最终结果写入。
            if self.output_file and entry.stable and entry.translation:
                self.output_file.write(entry.text + '\n' + ' '.join(entry.translation.split()) + '\n')
                self.output_file.flush()
            self.entries.pop(entry.id, None)
        draft = [line for entry in self.finalized + self.active for line in self.lines(entry)]
        if self.web_state:
            web_draft = [WebState.item(e, e.stable) for e in self.finalized]
            web_draft += [WebState.item(e, False) for e in self.active]
            self.web_state.publish(web_committed, web_draft)
        self.terminal.update(committed, draft)
        self.changed = False

    def tick(self, force=False):
        """轮询异步翻译结果，发生变化时更新显示。"""
        self.translator.tick(force)
        self.render()

    def finish(self):
        """停止时等待剩余翻译并清理线程；未稳定末句只展示，不写结果文件。"""
        self.finalized.extend(self.active)
        self.active = []
        self.carry = ''
        self.changed = True
        # 停止时处理剩余请求；单次 HTTP 请求有超时，避免无限等待。
        try:
            while self.translator.pending or self.translator.inflight:
                self.tick(force=True)
                time.sleep(0.02)
            self.render()
        finally:
            self.translator.close()


def normalize(text):
    # 音乐、静音等标记不能作为正文进入翻译和朗读。
    """移除已知非语音标记并整理空白，不擅自更正人名或术语。"""
    text = re.sub(r'\[(?:(?:audience|crowd)\s+)?(?:BLANK_AUDIO|silence|music|applause|laughter|cheering|cheers)[^\]]*\]', '', text, flags=re.I)
    text = re.sub(r'\((?:(?:upbeat|dramatic|soft|background|instrumental)\s+)?'
                  r'(?:(?:audience|crowd)\s+)?'
                  r'(?:music|silence|applause|laughter|laughing|inaudible|cheering|cheers)\)', '', text, flags=re.I)
    # 删除声音标记后剩下的单独破折号也不属于语音正文。
    if not text.strip(' \t-–—'):
        return ''
    return re.sub(r'\s+([,.!?。！？])', r'\1', ' '.join(text.split()))


def sentences(text):
    """按句末标点和英文缩写分句，返回完整句子及待后续拼接的半句。"""
    complete = []
    start = 0
    abbreviations = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'e.g', 'i.e'}
    for match in re.finditer(r'[.!?。！？]+["\u201d\u2019\')\]]*(?=\s|$)', text):
        punctuation = match.start()
        if text[punctuation] == '.':
            token = text[start:punctuation].split()[-1:] or ['']
            word = token[0].strip('"\u201c( ').lower()
            if word in abbreviations or re.fullmatch(r'(?:[a-z]\.)*[a-z]', word):
                continue
        complete.append(text[start:match.end()].strip())
        start = match.end()
    repaired = []
    pending = ''
    for sentence in complete:
        sentence = join_fragment(pending, sentence)
        if incomplete(sentence):
            pending = sentence.rstrip('.').rstrip()
        else:
            repaired.append(sentence)
            pending = ''
    return repaired, join_fragment(pending, text[start:].strip())


class StreamParser:
    """解释 Whisper 的回车和 ANSI 清行指令，而非直接累加终端文字。"""

    def __init__(self, callback):
        """初始化行缓冲、控制序列缓冲及识别块版本。"""
        self.callback = callback
        self.line = ''
        self.cursor = 0
        self.escape = ''
        self.started = False
        self.revision = 0
        self.block = 0
        self.dirty = False

    def feed(self, text):
        """逐字符消费输出；兼容跨读取块的控制序列，换行时结束识别块。"""
        for char in text:
            if self.escape:
                self.escape += char
                if self.escape == '\x1b[':
                    continue
                if len(self.escape) > 2 and '@' <= char <= '~':
                    if self.escape.endswith('K'):
                        if self.escape == '\x1b[2K':
                            self.line = ''
                        else:
                            self.line = self.line[:self.cursor]
                        self.dirty = True
                    self.escape = ''
                elif len(self.escape) > 64:
                    self.escape = ''
                continue
            # 控制序列可能在任意字节边界拆开，escape 必须跨 feed 调用保留。
            if char == '\x1b':
                self.escape = char
            elif char == '\r':
                self.cursor = 0
            elif char == '\n':
                if not self.started:
                    self.started = self.line.strip() == '[Start speaking]'
                elif self.line.strip():
                    self.publish(final=True)
                    self.block += 1
                self.line = ''
                self.cursor = 0
                self.dirty = False
            elif char == '\b':
                self.cursor = max(0, self.cursor - 1)
            elif char >= ' ' or char == '\t':
                self.line = self.line[:self.cursor].ljust(self.cursor) + char + self.line[self.cursor + 1:]
                self.cursor += 1
                self.dirty = True

    def publish(self, final=False):
        """发布非空源行的最新快照；纯静音标记可归一化为空文本。"""
        text = normalize(self.line)
        if self.started and self.line.strip() and (self.dirty or final):
            self.revision += 1
            self.callback(text, final, self.block, self.revision)
            self.dirty = False


class Terminal:
    """管理固定文本和可重绘文本，不把临时版本写进重定向输出。"""
    def __init__(self, output):
        """记录输出是否为交互终端及其初始宽度。"""
        self.output = output
        self.tty = output.isatty()
        self.rows = 0
        self.width = shutil.get_terminal_size((80, 24)).columns

    def clear_draft(self):
        """按上次占用的物理行数回退并清除临时区域。"""
        if self.tty and self.rows:
            self.output.write('\r')
            if self.rows > 1:
                self.output.write(f'\x1b[{self.rows - 1}A')
            self.output.write('\x1b[J')
        self.rows = 0

    def update(self, committed, draft):
        """先写固定行，再重绘临时行，并计算中文宽字符带来的自动折行。"""
        self.clear_draft()
        for sentence in committed:
            self.output.write(sentence + '\n')
        if self.tty and draft:
            self.output.write('\n'.join(draft))
            # 按终端物理行计数，中文宽字符和自动折行都会影响回退距离。
            self.rows = 0
            for line in draft:
                width = sum(0 if unicodedata.combining(c) else
                            2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in line)
                self.rows += max(1, (width + self.width - 1) // self.width)
        self.output.flush()

    def finish(self, tail):
        """清除临时区并输出剩余尾句。"""
        self.update([tail] if tail else [], [])


class Transcript:
    """提供禁用翻译时的纯英文分句和输出。"""
    def __init__(self, terminal, event_file=None, output_file=None):
        """初始化纯英文模式的显示、日志和半句缓冲。"""
        self.terminal = terminal
        self.event_file = event_file
        self.output_file = output_file
        self.carry = ''
        self.latest = ''

    def accept(self, text, final, block, revision):
        """更新当前英文块；只把结束块中的完整句子写入稳定文件。"""
        self.latest = normalize(join_fragment(self.carry, text))
        complete, tail = sentences(self.latest)
        if self.event_file:
            self.event_file.write(json.dumps(dict(time=time.time(), block=block, revision=revision,
                                                 type='block_end' if final else 'replace',
                                                 text=self.latest), ensure_ascii=False) + '\n')
            self.event_file.flush()
        if final:
            if self.output_file and complete:
                self.output_file.write('\n'.join(complete) + '\n')
                self.output_file.flush()
            self.carry = tail
            self.latest = tail
            self.terminal.update(complete, [tail] if tail else [])
        else:
            # 有句号也不代表已固定：Whisper 结束当前块前仍允许修订。
            self.terminal.update([], complete + ([tail] if tail else []))

    def finish(self):
        """输出未结束的尾句，但不将其当作稳定结果写入文件。"""
        complete, tail = sentences(self.latest)
        self.terminal.update(complete + ([tail] if tail else []), [])
        self.latest = self.carry = ''


def lan_address():
    """获取默认路由使用的本机 IPv4；UDP connect 仅选路，不发送数据。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 80))
            address = probe.getsockname()[0]
            if address != '0.0.0.0' and not address.startswith('127.'):
                return address
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address != '0.0.0.0' and not address.startswith(('127.', '169.254.')):
                return address
    except OSError:
        pass
    return None


def main():
    """解析参数，启动 Whisper 与可选 HTTP 服务，并统一管理退出和资源清理。"""
    root = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture', type=int, default=2, help='SDL capture index (BlackHole currently 2)')
    ap.add_argument('-m', '--model', type=Path, default=root / 'models/ggml-tiny.en.bin',
                    help='model file passed to whisper-stream -m')
    ap.add_argument('--binary', type=Path, default=root / 'bin/whisper-stream')
    ap.add_argument('--threads', type=int, default=9)
    ap.add_argument('--log', type=Path, default=root / 'whisper.log')
    ap.add_argument('--events', type=Path, nargs='?', const=root / 'log.jsonl',
                    help='JSONL event log (default path when enabled: log.jsonl)')
    ap.add_argument('--output', type=Path, default=root / 'output.txt', help='append stable results and command here')
    ap.add_argument('--no-output', action='store_true', help='disable transcript file')
    ap.add_argument('--no-translate', action='store_true', help='English-only output; no Google requests')
    ap.add_argument('--translation-workers', type=int, default=4, help='concurrent Google requests')
    ap.add_argument('--batch-wait-ms', type=int, default=50, help='short coalescing delay for translation batches')
    ap.add_argument('--replay', type=Path, help='replay raw stdout recording instead of recording audio')
    ap.add_argument('--web', action='store_true', help='enable local HTTP client')
    ap.add_argument('--host', default='0.0.0.0', help='HTTP bind address')
    ap.add_argument('--port', type=int, default=8765, help='HTTP port')
    ap.add_argument('--proxy-host', help='SSH host alias, e.g. remote-host; enables --web and reverse tunnel')
    ap.add_argument('--proxy-tunnel-port', type=int, default=18765, help='remote loopback SSH tunnel port')
    ap.add_argument('--proxy-log', type=Path, default=root / 'proxy.log', help='SSH tunnel log')
    args = ap.parse_args()
    if args.proxy_host:
        args.web = True
        if args.host not in ('0.0.0.0', '127.0.0.1', 'localhost') or args.port == 0:
            ap.error('--proxy-host requires a loopback-accessible HTTP host and a fixed port')
        if args.proxy_tunnel_port == args.port:
            ap.error('local HTTP port and remote tunnel port must differ')
        if not shutil.which('ssh'):
            ap.error('ssh executable not found')
        from reverse_proxy.tunnel import ssh_command
        try:
            ssh_command(args.proxy_host, args.port, args.proxy_tunnel_port)
        except ValueError as exc:
            ap.error(str(exc))
    if args.threads < 1:
        ap.error('threads must be positive')
    if not 0 <= args.port <= 65535:
        ap.error('port must be 0..65535')
    if args.web and args.no_translate:
        ap.error('--web requires translation; remove --no-translate')
    if not 1 <= args.translation_workers <= 16 or not 0 <= args.batch_wait_ms <= 1000:
        ap.error('translation-workers must be 1..16; batch-wait-ms must be 0..1000')
    if not args.replay:
        for path in (args.binary, args.model):
            if not path.is_file():
                ap.error(f'file not found: {path}')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    if args.events:
        args.events.parent.mkdir(parents=True, exist_ok=True)
    events = args.events.open('w') if args.events else None
    command = [str(args.binary.resolve()), '-ng', '-t', str(args.threads), '-m',
               str(args.model.resolve()), '-l', 'en', '-c', str(args.capture)]
    web_state = WebState() if args.web else None
    server = None
    if web_state:
        try:
            server = start_web(web_state, args.host, args.port)
        except OSError as exc:
            ap.error(f'HTTP server: {exc}')
        display_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
        print(f'Web client: http://{display_host}:{server.server_port} (listening on {args.host})', file=sys.stderr)
        if args.host == '0.0.0.0':
            address = lan_address()
            if address:
                print(f'LAN client: http://{address}:{server.server_port}', file=sys.stderr)
            else:
                print('LAN client: unable to detect local IPv4 address', file=sys.stderr)
    output_file = None
    if not args.no_output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output_file = args.output.open('a', encoding='utf-8')
        output_file.write('\n# ' + time.strftime('%Y-%m-%d %H:%M:%S %z') + '\n')
        output_file.write('# Command: ' + shlex.join([sys.executable, *sys.argv]) + '\n')
        if not args.replay:
            output_file.write('# Whisper: ' + shlex.join(command) + '\n')
        output_file.flush()
    if args.no_translate:
        transcript = Transcript(Terminal(sys.stdout), events, output_file)
    else:
        transcript = BilingualTranscript(Terminal(sys.stdout), normalize, sentences, events, output_file,
                                         web_state=web_state,
                                         workers=args.translation_workers,
                                         batch_wait=args.batch_wait_ms / 1000)

    def tick():
        """只在双语模式下推进异步翻译，纯英文模式无需调度。"""
        if not args.no_translate:
            transcript.tick()
    parser = StreamParser(transcript.accept)
    # UTF-8 多字节字符可能被管道读取拆开，不能逐块直接 decode。
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    process = None
    tunnel = None
    try:
        if args.proxy_host:
            from reverse_proxy.tunnel import ReverseTunnel
            tunnel = ReverseTunnel(args.proxy_host, server.server_port,
                                   args.proxy_tunnel_port, args.proxy_log)
            tunnel.start()
            print(f'Remote proxy: SSH {args.proxy_host}, remote loopback port {args.proxy_tunnel_port} -> local HTTP {args.port}; log: {args.proxy_log}', file=sys.stderr)
        if args.replay:
            with args.replay.open('rb') as source:
                while data := source.read(4096):
                    parser.feed(decoder.decode(data))
                    parser.publish()
                    tick()
            return 0
        print(f'Listening on capture #{args.capture}. Ctrl+C to stop. Diagnostics: {args.log}', file=sys.stderr)
        with args.log.open('w') as log:
            # 诊断信息单独写 stderr 日志，避免混入识别文本和翻译请求。
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    ready = selector.select(timeout=0.08)
                    if not ready:
                        parser.publish()
                        tick()
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        break
                    parser.feed(decoder.decode(data))
                    tick()
            code = process.wait()
            if code:
                print(f'whisper-stream exited ({code}). See {args.log}', file=sys.stderr)
            return code
    except KeyboardInterrupt:
        return 130
    finally:
        # 进入退出流程就停止重连，避免等待翻译结束期间又建立新隧道。
        if tunnel:
            tunnel.close()
        # 先中断、再终止、最后强制结束整个子进程组，避免退出后残留录音进程。
        if process:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
            if process.stdout:
                # 先读完子进程已刷出的最后一段结果，再关闭输出管道。
                while data := process.stdout.read(65536):
                    parser.feed(decoder.decode(data))
                process.stdout.close()
        parser.feed(decoder.decode(b'', final=True))
        parser.publish()
        try:
            transcript.finish()
        finally:
            if events:
                events.close()
            if output_file:
                output_file.close()
            if server:
                web_state.close()
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    sys.exit(main())
