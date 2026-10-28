"""代理测试只使用模拟 SSH 和配置渲染，不连接或部署服务器。"""

from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import sys
import os
import json
from unittest.mock import patch

from reverse_proxy.tunnel import ReverseTunnel, ssh_command


class TunnelTests(unittest.TestCase):
    def test_deploy_uploads_script_then_requests_sudo_on_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'calls.jsonl'
            ssh = root / 'ssh'
            ssh.write_text('#!' + sys.executable + '\n' +
                           'import os,sys,json\n'
                           'with open(os.environ["PROXY_TEST_CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                           'if "mktemp -d" in sys.argv[-1]: print("/tmp/live-translate.Test123")\n'
                           'elif "cat >" in sys.argv[-1]: sys.stdin.read()\n')
            ssh.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], PROXY_TEST_CALLS=str(log))
            master, slave = os.openpty()
            try:
                result = subprocess.run(['bash', str(Path(__file__).with_name('deploy.sh')), 'remote-host', 'live-translate.example.com'],
                                        stdin=slave, capture_output=True, text=True, env=env, timeout=5)
            finally:
                os.close(master)
                os.close(slave)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(calls), 4)
            self.assertIn('cat >', calls[1][-1])
            self.assertIn('-t', calls[2])
            self.assertIn('sudo bash', calls[2][-1])
            self.assertIn('$(id -u)', calls[2][-1])  # 必须在远端判断 root，不能被本机提前展开。
            self.assertNotIn('sudo -n', calls[2][-1])
            self.assertIn('rmdir', calls[3][-1])

    def test_main_rejects_invalid_proxy_options_before_startup(self):
        main = Path(__file__).resolve().parent.parent / 'live_translate.py'
        for options in [ ['--proxy-host=-bad'],
                         ['--proxy-host=remote-host', '--proxy-tunnel-port=8765'],
                         ['--proxy-host=remote-host', '--no-translate'] ]:
            result = subprocess.run([sys.executable, str(main), *options], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_ssh_command_uses_private_remote_bind_and_keepalive(self):
        command = ssh_command('remote-host', 8765, 18765)
        self.assertEqual(command[-1], 'remote-host')
        self.assertIn('127.0.0.1:18765:127.0.0.1:8765', command)
        self.assertIn('ExitOnForwardFailure=yes', command)
        self.assertIn('BatchMode=yes', command)
        self.assertIn('ControlPath=none', command)
        self.assertNotIn('-f', command)

    def test_rejects_option_injection_and_invalid_ports(self):
        for host in ['-oProxyCommand=bad', 'remote-host -R 80:host:80', '']:
            with self.assertRaises(ValueError):
                ssh_command(host, 8765, 18765)
        for port in [0, -1, 65536]:
            with self.assertRaises(ValueError):
                ssh_command('remote-host', port, 18765)

    def test_close_stops_only_own_ssh_child(self):
        started, stopped = threading.Event(), threading.Event()
        class Process:
            def __init__(self, *args, **kwargs):
                started.set()
            def poll(self):
                return 0 if stopped.is_set() else None
            def wait(self, timeout=None):
                stopped.wait(timeout or 2)
                return 0
            def terminate(self):
                stopped.set()
            def kill(self):
                stopped.set()
        with tempfile.TemporaryDirectory() as directory, patch('reverse_proxy.tunnel.subprocess.Popen', Process):
            tunnel = ReverseTunnel('remote-host', 8765, 18765, Path(directory) / 'proxy.log')
            tunnel.start()
            self.assertTrue(started.wait(1))
            tunnel.close()
            self.assertTrue(stopped.is_set())
            self.assertFalse(tunnel.thread.is_alive())
            self.assertNotIn('retry', (Path(directory) / 'proxy.log').read_text())

    def test_unexpected_ssh_exit_is_logged_and_retried(self):
        reconnected, stopped = threading.Event(), threading.Event()
        processes = []
        class Process:
            def __init__(self, *args, **kwargs):
                processes.append(self)
                self.first = len(processes) == 1
                if not self.first:
                    reconnected.set()
            def wait(self, timeout=None):
                if self.first:
                    return 255
                stopped.wait(timeout)
                return 0
            def poll(self):
                return 255 if self.first else (0 if stopped.is_set() else None)
            def terminate(self):
                stopped.set()
            def kill(self):
                stopped.set()
        with tempfile.TemporaryDirectory() as directory, patch('reverse_proxy.tunnel.subprocess.Popen', Process):
            log = Path(directory) / 'proxy.log'
            tunnel = ReverseTunnel('remote-host', 8765, 18765, log)
            tunnel.start()
            try:
                self.assertTrue(reconnected.wait(3), 'SSH did not reconnect after unexpected exit')
                self.assertEqual(len(processes), 2)
                self.assertIn('SSH exited (255); retry in 1s', log.read_text())
            finally:
                tunnel.close()
            self.assertTrue(stopped.is_set())
            self.assertFalse(tunnel.thread.is_alive())
            self.assertNotIn('SSH exited (0)', log.read_text())

    def test_nginx_config_sse_and_port_validation(self):
        script = Path(__file__).with_name('install.sh')
        result = subprocess.run(['bash', str(script), 'live-translate.example.com', '18765', '--print-config'],
                                capture_output=True, text=True, check=True)
        self.assertIn('listen 443 ssl;', result.stdout)
        self.assertIn('server_name live-translate.example.com;', result.stdout)
        self.assertIn('/root/cert/live-translate.example.com/fullchain.pem;', result.stdout)
        self.assertIn('return 301 https://live-translate.example.com$request_uri;', result.stdout)
        self.assertIn('proxy_set_header X-Forwarded-Proto $scheme;', result.stdout)
        self.assertNotIn('listen 0.0.0.0:8765', result.stdout)
        self.assertIn('proxy_pass http://127.0.0.1:18765;', result.stdout)
        self.assertIn('proxy_buffering off;', result.stdout)
        self.assertIn('proxy_read_timeout 3600s;', result.stdout)
        self.assertIn('proxy_set_header Host $http_host;', result.stdout)
        for ports in [('live_translate.example.com', '18765'), ('-bad.example.com', '18765'), ('a..com', '18765'), ('a.com', '0'), ('a.com', '443'), ('a.com;', '18765')]:
            failure = subprocess.run(['bash', str(script), *ports, '--print-config'], capture_output=True)
            self.assertNotEqual(failure.returncode, 0)


if __name__ == '__main__':
    unittest.main()
