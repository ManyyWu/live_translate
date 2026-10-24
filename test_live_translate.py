import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os
import re
import shutil
import signal
import time
import wave

from live_translate import StreamParser, Terminal, Transcript, sentences, normalize


class LiveTextTests(unittest.TestCase):
    def test_fragmented_clear_and_revision(self):
        events = []
        parser = StreamParser(lambda *event: events.append(event))
        for char in '[Start speaking]\n\x1b[2K\r' + ' ' * 100 + '\x1b[2K\rHello cars':
            parser.feed(char)
        parser.publish()
        for char in '\x1b[2K\rHello car.':
            parser.feed(char)
        parser.publish()
        parser.feed('\n')
        self.assertEqual([(e[0], e[1]) for e in events],
                         [('Hello cars', False), ('Hello car.', False), ('Hello car.', True)])

    def test_revised_sentence_and_cross_block_join(self):
        output = io.StringIO()
        transcript = Transcript(Terminal(output))
        transcript.accept('Hello cat.', False, 0, 1)
        transcript.accept('Hello car. Next', True, 0, 2)
        transcript.accept('sentence is wrong.', False, 1, 3)
        transcript.accept('sentence is right. Tail', True, 1, 4)
        transcript.finish()
        self.assertEqual(output.getvalue(), 'Hello car.\nNext sentence is right.\nTail\n')

    def test_english_boundaries(self):
        self.assertEqual(sentences('Dr. Smith paid 3.14 dollars. "Really?" Not yet'),
                         (['Dr. Smith paid 3.14 dollars.', '"Really?"'], 'Not yet'))

    def test_sound_markers_and_fragment_repair(self):
        self.assertEqual(normalize('(upbeat music) Hello (dramatic music).'), 'Hello.')
        self.assertEqual(normalize('(upbeat music) -'), '')
        self.assertEqual(normalize('He said (in theory) it works.'), 'He said (in theory) it works.')
        self.assertEqual(sentences('The roadster was.'), ([], 'The roadster was'))
        self.assertEqual(sentences('The roadster was. was announced in 2017.'),
                         (['The roadster was announced in 2017.'], ''))
        self.assertEqual(sentences('Yes. Yes. It is.'), (['Yes.', 'Yes.', 'It is.'], ''))
        self.assertEqual(sentences("It works like that. It hasn't been."),
                         (["It works like that.", "It hasn't been."], ''))

    def test_silence_replaces_draft(self):
        events = []
        parser = StreamParser(lambda *event: events.append(event))
        parser.feed('[Start speaking]\nHello')
        parser.publish()
        parser.feed('\x1b[2K\r[Silence]')
        parser.publish()
        self.assertEqual(events[-1][0], '')

    def test_cheering_markers_keep_spoken_text(self):
        for source, expected in [
            ("[CHEERING] See no three, let's down the two.", "See no three, let's down the two."),
            ('(audience cheering) College of Blue London.', 'College of Blue London.'),
            ('(audience cheering) (audience cheering) All right.', 'All right.'),
            ('[crowd cheering] (Crowd Cheering) -', ''),
            ('The audience is cheering. (in theory)', 'The audience is cheering. (in theory)'),
        ]:
            with self.subTest(source=source):
                self.assertEqual(normalize(source), expected)

    def test_child_stream_live_revisions_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-stream'
            binary.write_text(
                '#!/usr/bin/env python3\n'
                'import sys,time\n'
                'print("diagnostic only",file=sys.stderr)\n'
                'print("[Start speaking]",flush=True)\n'
                'sys.stdout.write("\\x1b[2K\\rHello cats.");sys.stdout.flush();time.sleep(.3)\n'
                'sys.stdout.write("\\x1b[2K\\rHello car. Next\\n");sys.stdout.flush()\n'
                'sys.stdout.write("\\x1b[2K\\rsentence.\\n");sys.stdout.flush()\n')
            binary.chmod(0o755)
            model = root / 'model'
            model.touch()
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('live_translate.py')),
                                     '--binary', str(binary), '-m', str(model),
                                     '--no-translate',
                                     '--output', str(root / 'stable.txt'),
                                     '--log', str(root / 'log'), '--events', str(root / 'events')],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'Hello car.\nNext sentence.\n')
            events = [json.loads(line) for line in (root / 'events').read_text().splitlines()]
            self.assertEqual(events[0]['text'], 'Hello cats.')
            self.assertEqual(events[0]['type'], 'replace')
            self.assertEqual((root / 'log').read_text().strip(), 'diagnostic only')
            saved = (root / 'stable.txt').read_text()
            self.assertIn('# Command:', saved)
            self.assertIn('# Whisper:', saved)
            self.assertTrue(saved.endswith('Hello car.\nNext sentence.\n'))
            self.assertNotIn('Hello cats.', saved)





from concurrent.futures import Future
import io
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from live_translate import BilingualTranscript
from live_translate import normalize, sentences
from live_translate import google_translate


class Display:
    def __init__(self):
        self.committed = []
        self.draft = []

    def update(self, committed, draft):
        self.committed.extend(committed)
        self.draft = draft


def until(transcript, condition):
    deadline = time.monotonic() + 2
    while not condition():
        transcript.tick(force=True)
        if time.monotonic() > deadline:
            raise AssertionError('translation worker did not complete')
        time.sleep(0.005)


class TranslationTests(unittest.TestCase):
    def test_fragment_waits_for_next_block_before_file_commit(self):
        saved = io.StringIO()
        display = Display()
        transcript = BilingualTranscript(display, normalize, sentences, output_file=saved,
                                         transport=lambda texts: ['译：' + t for t in texts])
        transcript.accept('(upbeat music) The roadster was.', True, 0, 1)
        until(transcript, lambda: bool(transcript.active[0].translation))
        self.assertEqual(saved.getvalue(), '')
        transcript.accept('was announced in 2017.', True, 1, 2)
        transcript.finish()
        self.assertEqual(saved.getvalue(),
                         'The roadster was announced in 2017.\n译：The roadster was announced in 2017.\n')

    def test_file_saves_only_stable_current_translation_in_order(self):
        saved = io.StringIO()
        transcript = BilingualTranscript(Display(), normalize, sentences, output_file=saved,
                                         transport=lambda texts: ['译：' + t for t in texts])
        transcript.accept('Old.', False, 0, 1)
        until(transcript, lambda: bool(transcript.active[0].translation))
        self.assertEqual(saved.getvalue(), '')
        transcript.accept('New. Unfinished', True, 0, 2)
        self.assertEqual(saved.getvalue(), '')  # Previous translation is still only a display fallback.
        until(transcript, lambda: not transcript.finalized)
        self.assertEqual(saved.getvalue(), 'New.\n译：New.\n')
        transcript.finish()
        self.assertEqual(saved.getvalue(), 'New.\n译：New.\n')  # Stop does not stabilize a partial tail.

    def test_file_does_not_save_failed_or_stale_translation(self):
        saved = io.StringIO()
        def fail(texts):
            raise TimeoutError('test')
        transcript = BilingualTranscript(Display(), normalize, sentences, output_file=saved,
                                         transport=fail)
        transcript.accept('Old.', False, 0, 1)
        entry = transcript.active[0]
        transcript.result(entry.id, 1, 1, '旧译文', '')
        transcript.accept('New.', True, 0, 2)
        transcript.finish()
        self.assertEqual(saved.getvalue(), '')

    def test_keep_previous_translation_until_latest_version_arrives(self):
        display = Display()
        transcript = BilingualTranscript(display, normalize, sentences,
                                         transport=lambda texts: ['新译文'] * len(texts))
        try:
            transcript.accept('First.', False, 0, 1)
            entry = transcript.active[0]
            transcript.result(entry.id, 1, 1, '原译文', '')
            transcript.accept('Revised.', False, 0, 2)
            self.assertEqual(display.draft, ['Revised.', '原译文'])
            transcript.accept('Latest.', True, 0, 3)
            self.assertEqual(display.draft, ['Latest.', '原译文'])
            self.assertEqual(display.committed, [])  # Old text must not finalize the new version.
            transcript.result(entry.id, 2, 2, '过期译文', '')
            transcript.render()
            self.assertEqual(display.draft, ['Latest.', '原译文'])
            transcript.result(entry.id, 3, 3, '最新译文', '')
            transcript.render()
            self.assertEqual(display.committed, ['Latest.', '最新译文'])
        finally:
            transcript.finish()

    def test_web_batch_encoding_and_unpack(self):
        def response(request, timeout):
            self.assertEqual(parse_qs(request.data.decode())['q'], ['A & B?', 'Next sentence.'])
            self.assertEqual(timeout, 8)
            return io.BytesIO(json.dumps(['甲和乙？', ['下一句。', 'en']]).encode())
        with patch('live_translate.urlopen', response):
            self.assertEqual(google_translate(['A & B?', 'Next sentence.']), ['甲和乙？', '下一句。'])
        with patch('live_translate.urlopen', return_value=io.BytesIO(b'[]')):
            with self.assertRaises(ValueError):
                google_translate(['one'])

    def test_pending_entries_are_batched_and_revisions_coalesced(self):
        calls = []
        display = Display()
        def translate(texts):
            calls.append(texts)
            return ['中：' + text for text in texts]
        transcript = BilingualTranscript(display, normalize, sentences, transport=translate)
        try:
            transcript.accept('Old. Second', False, 0, 1)
            transcript.accept('New. Second sentence.', False, 0, 2)
            transcript.tick(force=True)
            until(transcript, lambda: all(e.translation for e in transcript.active))
            self.assertEqual(calls, [['New.', 'Second sentence.']])
            self.assertEqual(display.draft, ['New.', '中：New.', 'Second sentence.', '中：Second sentence.'])
            self.assertEqual(display.committed, [])  # Unstable text is translated too.
        finally:
            transcript.finish()

    def test_out_of_order_batches_unpack_per_entry(self):
        release_old = threading.Event()
        started_old = threading.Event()
        display = Display()
        log = io.StringIO()
        def translate(texts):
            if texts[0] == 'Old.':
                started_old.set()
                release_old.wait(2)
            return ['中：' + text for text in texts]
        transcript = BilingualTranscript(display, normalize, sentences, log,
                                         transport=translate, workers=2)
        try:
            transcript.accept('Old. Unchanged.', False, 0, 1)
            transcript.tick(force=True)
            self.assertTrue(started_old.wait(1))
            transcript.accept('New. Unchanged.', False, 0, 2)
            self.assertEqual(display.draft[:2], ['New.', '[翻译中…]'])
            transcript.tick(force=True)
            until(transcript, lambda: transcript.active[0].translation == '中：New.')
            self.assertFalse(release_old.is_set())  # New request finished while old one was blocked.
            release_old.set()
            until(transcript, lambda: not transcript.translator.inflight)
            self.assertEqual(display.draft, ['New.', '中：New.', 'Unchanged.', '中：Unchanged.'])
            events = [json.loads(line) for line in log.getvalue().splitlines()]
            self.assertTrue(any(e['type'] == 'translation_discarded' for e in events))
            self.assertTrue(all('sent_at' in e for e in events if e['type'] == 'batch_sent'))
        finally:
            release_old.set()
            transcript.finish()

    def test_removed_sentence_response_and_older_batch_are_ignored(self):
        transcript = BilingualTranscript(Display(), normalize, sentences,
                                         transport=lambda texts: ['中'] * len(texts))
        try:
            transcript.accept('First. Second.', False, 0, 1)
            removed = transcript.active[1].id
            transcript.accept('Merged sentence.', False, 0, 2)
            entry = transcript.active[0]
            transcript.result(removed, 1, 3, '已删除', '')
            transcript.result(entry.id, entry.version, 5, '新包', '')
            transcript.result(entry.id, entry.version, 4, '旧包', '')
            self.assertEqual(entry.translation, '新包')
            self.assertNotIn(removed, transcript.entries)
        finally:
            transcript.finish()

    def test_failure_keeps_two_lines_and_does_not_block_later_sentences(self):
        def fail(texts):
            raise TimeoutError('test timeout')
        display = Display()
        transcript = BilingualTranscript(display, normalize, sentences, transport=fail)
        transcript.accept('Hello. Next', True, 0, 1)
        transcript.finish()
        self.assertEqual(display.committed, ['Hello.', '[翻译失败]', 'Next', '[翻译失败]'])

    def test_sentence_order_cross_block_carry_and_cache(self):
        calls = []
        def translate(texts):
            calls.append(texts)
            return ['中：' + t for t in texts]
        display = Display()
        transcript = BilingualTranscript(display, normalize, sentences, transport=translate)
        transcript.accept('Hello. Next', True, 0, 1)
        until(transcript, lambda: not transcript.finalized and bool(transcript.active[0].translation))
        transcript.accept('sentence.', True, 1, 2)
        transcript.finish()
        self.assertEqual(display.committed, ['Hello.', '中：Hello.', 'Next sentence.', '中：Next sentence.'])





class WebTests(unittest.TestCase):
    def test_reverse_results_do_not_open_playback_before_previous_sentence(self):
        from live_translate import WebState
        state = WebState()
        transcript = BilingualTranscript(Display(), normalize, sentences, web_state=state,
                                         transport=lambda texts: ['中'] * len(texts))
        try:
            transcript.accept('First. Second.', True, 0, 1)
            baseline = state.seq
            first, second = transcript.finalized
            transcript.result(second.id, 1, 1, '第二句', '')
            transcript.render()
            self.assertFalse(state.snapshot()['items'][1]['playable'])
            transcript.result(first.id, 1, 2, '第一句', '')
            transcript.render()
            self.assertEqual([i['translation'] for i in state.snapshot()['items']], ['第一句', '第二句'])
            self.assertTrue(all(i['playable'] for i in state.snapshot()['items']))
            # 客户端跳过中间的“第二句先完成”事件后，合并包也必须先发第一句。
            self.assertEqual([i['translation'] for i in state.since(baseline)['upsert']], ['第一句', '第二句'])
        finally:
            transcript.translator.close()

    def test_shutdown_tail_never_becomes_stable_or_playable(self):
        from live_translate import WebState
        state = WebState()
        observed = []
        publish = state.publish
        def record(committed, draft):
            observed.extend(dict(i) for i in committed + draft)
            publish(committed, draft)
        state.publish = record
        def delayed(texts):
            time.sleep(.03)
            return ['译文'] * len(texts)
        transcript = BilingualTranscript(Display(), normalize, sentences, web_state=state, transport=delayed)
        transcript.accept('First. Tail', True, 0, 1)
        tail = transcript.active[0]
        transcript.result(tail.id, 1, 999, '末句', '')
        transcript.render()
        transcript.finish()
        self.assertTrue(any(i['text'] == 'Tail' for i in observed))
        self.assertFalse(any(i['text'] == 'Tail' and (i['stable'] or i['playable']) for i in observed))

    def test_delta_merge_removal_and_expired_history_recovery(self):
        from live_translate import WebState
        state = WebState()
        first_session = state.session
        self.assertNotEqual(first_session, WebState().session)
        state.publish([], [dict(id='one', text='Old'), dict(id='two', text='Removed')])
        state.publish([dict(id='one', text='Final')], [])
        delta = state.since(1)
        self.assertEqual(delta['type'], 'delta')
        self.assertEqual(delta['upsert'], [dict(id='one', text='Final')])
        self.assertEqual(delta['remove'], ['two'])
        self.assertEqual(state.since(-1)['type'], 'snapshot')
        for version in range(260):
            state.publish([], [dict(id='draft', text=str(version))])
        self.assertEqual(state.since(0)['type'], 'snapshot')
        recent = state.since(state.seq - 1)
        self.assertEqual(len(recent['upsert']), 1)
        self.assertNotIn('items', recent)  # 增量不能夹带所有历史。
        state.close()
        self.assertTrue(state.since(state.seq - 1)['closed'])

    def test_current_session_only_and_live_translation_versions(self):
        from live_translate import WebState
        state = WebState()
        self.assertEqual(state.snapshot()['items'], [])
        transcript = BilingualTranscript(Display(), normalize, sentences, web_state=state,
                                         transport=lambda texts: ['中：' + t for t in texts])
        try:
            transcript.accept('Old.', False, 0, 1)
            until(transcript, lambda: bool(transcript.active[0].translation))
            item = state.snapshot()['items'][-1]
            self.assertFalse(item['stable'])
            self.assertTrue(item['ready'])
            transcript.accept('New.', True, 0, 2)
            item = state.snapshot()['items'][-1]
            self.assertTrue(item['stable'])
            self.assertFalse(item['ready'])  # Never speak the old visible translation of a new source.
            until(transcript, lambda: not transcript.finalized)
            self.assertEqual(state.snapshot()['items'][-1]['translation'], '中：New.')
            self.assertTrue(state.snapshot()['items'][-1]['ready'])
        finally:
            transcript.finish()

    def test_http_snapshot_assets_sse_and_path_isolation(self):
        from live_translate import WebState, start_web
        from urllib.request import urlopen as http_open
        from urllib.error import HTTPError
        state = WebState()
        server = start_web(state, '127.0.0.1', 0)
        address = f'http://127.0.0.1:{server.server_port}'
        try:
            with http_open(address + '/api/state', timeout=2) as response:
                self.assertEqual(json.load(response)['items'], [])
            for path, expected in [('/', b'<title>'), ('/app.js', b'localService'), ('/style.css', b':root')]:
                with http_open(address + path, timeout=2) as response:
                    self.assertIn(expected, response.read())
            with self.assertRaises(HTTPError) as missing:
                http_open(address + '/live_translate.py', timeout=2)
            missing.exception.close()
            with http_open(address + '/api/events', timeout=2) as response:
                self.assertEqual(response.readline(), b'id: 0\n')
                self.assertEqual(json.loads(response.readline().decode()[6:])['seq'], 0)
                response.readline()
                state.publish([], [dict(id='live-1', text='Hi.', translation='你好。', stable=False, ready=True)])
                self.assertEqual(response.readline(), b'id: 1\n')
                self.assertEqual(json.loads(response.readline().decode()[6:])['upsert'][0]['translation'], '你好。')
            # A reconnect must immediately receive the latest full snapshot.
            with http_open(address + '/api/events', timeout=2) as response:
                self.assertEqual(response.readline(), b'id: 1\n')
        finally:
            with state.condition:
                state.closed = True
                state.condition.notify_all()
            server.shutdown()
            server.server_close()


def run_environment_checks():
    """Real integration checks, only enabled by direct script execution."""
    from live_translate import google_translate
    root = Path(__file__).resolve().parent
    failures = []

    def check(name, action):
        print(f'\n[检查] {name}', flush=True)
        try:
            detail = action()
            print(f'[通过] {detail or name}', flush=True)
        except Exception as exc:
            failures.append(name)
            print(f'[失败] {type(exc).__name__}: {exc}', flush=True)

    def command(arguments, timeout=15):
        result = subprocess.run([str(x) for x in arguments], cwd=root, capture_output=True,
                                text=True, errors='replace', timeout=timeout)
        output = result.stdout + result.stderr
        if result.returncode:
            raise RuntimeError(f'exit={result.returncode}\n{output[-3000:]}')
        return output

    def files():
        for name in ['bin/whisper-stream', 'bin/whisper-cli', 'models/ggml-tiny.en.bin',
                     'samples/1.wav', 'live-web/index.html', 'live-web/app.js', 'live-web/style.css']:
            path = root / name
            if not path.is_file():
                raise FileNotFoundError(path)
            if name.startswith('bin/') and not os.access(path, os.X_OK):
                raise PermissionError(f'not executable: {path}')
        return '运行文件、默认模型、样本和网页资源齐全'

    check('文件与可执行权限', files)
    check('Whisper CLI 和 stream 动态依赖/启动', lambda: (
        command([root / 'bin/whisper-cli', '--help']),
        command([root / 'bin/whisper-stream', '--help']), '两个可执行程序正常启动')[-1])

    def recognize():
        # Extract a real fifteen-second clip so memory and runtime stay bounded.
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / 'clip.wav'
            with wave.open(str(root / 'samples/1.wav'), 'rb') as source:
                if source.getnframes() / source.getframerate() < 1:
                    raise ValueError('sample audio is too short')
                with wave.open(str(clip), 'wb') as target:
                    target.setparams(source.getparams())
                    target.writeframes(source.readframes(15 * source.getframerate()))
            prefix = Path(directory) / 'recognized'
            start = time.monotonic()
            log = command([root / 'bin/whisper-cli', '-ng', '-t', '9', '-m',
                           root / 'models/ggml-tiny.en.bin', '-l', 'en', '-f', clip,
                           '-otxt', '-of', prefix], timeout=60)
            text = prefix.with_suffix('.txt').read_text().strip()
            if not re.search(r'[A-Za-z]', normalize(text)) or 'total time' not in log:
                raise ValueError(f'no recognizable output: {text!r}')
            return f'真实模型识别完成，耗时 {time.monotonic()-start:.2f}s；输出：{text[:200]}'
    check('样本格式、tiny 模型加载和 CPU 真实识别', recognize)

    def capture():
        # Invalid model exits after SDL enumeration, before entering the capture loop.
        probe = subprocess.run([str(root / 'bin/whisper-stream'), '-ng', '-m', os.devnull],
                               capture_output=True, text=True, errors='replace', timeout=15)
        listing = probe.stdout + probe.stderr
        match = re.search(r"Capture device #(\d+): '([^']*BlackHole[^']*)'", listing, re.I)
        if not match:
            raise RuntimeError('未发现 BlackHole。检查驱动、重启和终端录音权限。\n' + listing[:1800])
        arguments = [str(root / 'bin/whisper-stream'), '-ng', '-t', '9', '-m',
                     str(root / 'models/ggml-tiny.en.bin'), '-l', 'en', '-c', match[1]]
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors='replace', start_new_session=True)
        timed_out = False
        try:
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGINT)
                try:
                    output, _ = process.communicate(timeout=4)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    output, _ = process.communicate()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if '[Start speaking]' not in output or 'obtained spec' not in output:
            raise RuntimeError('实时采集未就绪：\n' + output[-2500:])
        if not timed_out and process.returncode:
            raise RuntimeError('实时进程异常退出：\n' + output[-2000:])
        speech = output.split('[Start speaking]', 1)[1].split('whisper_print_timings:', 1)[0]
        speech = re.sub(r'\x1b\[[0-9;]*[A-Za-z]|\[[^\]]*\]', '', speech).strip()
        suffix = '检测到识别输出' if speech else '当前无语音输出（可能未播放音频，不代表采集失败）'
        return f'{match[2]}，设备 #{match[1]} 已打开；{suffix}'
    check('SDL / BlackHole / 录音权限 / 实时采集（约10秒）', capture)
    check('Google 网页翻译真实批量请求', lambda: str(google_translate(['Hello, world.', 'This is a test.'])))

    def frontend():
        node = shutil.which('node')
        if not node:
            raise FileNotFoundError('node 未安装，不能运行前端自动测试')
        return command([node, '--test', 'test_live_web.cjs'], timeout=20).strip()
    check('网页语音、Cookie、队列、倍速与滚动测试', frontend)
    print('\n人工验证项：真实浏览器出声、手机语音引擎、音频路由隔离、识别准确率。', flush=True)
    print(f'环境检查结果：{len(failures)} 项失败' + ('：' + '、'.join(failures) if failures else ''), flush=True)
    return not failures


if __name__ == '__main__':
    # Direct execution always runs unit tests, actual HTTP/SSE tests and real dependencies.
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    environment_ok = run_environment_checks()
    sys.exit(0 if result.wasSuccessful() and environment_ok else 1)
