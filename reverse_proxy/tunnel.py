"""由本机建立 SSH 反向隧道；不在远端执行 Whisper 或翻译。"""

import subprocess
import threading


def ssh_command(host, local_port, tunnel_port):
    """使用独立 SSH 连接，远端转发端口只绑定回环地址。"""
    if not host or host.startswith('-') or any(c.isspace() for c in host):
        raise ValueError('proxy host must be an SSH alias or user@host without whitespace')
    if not 1 <= local_port <= 65535 or not 1 <= tunnel_port <= 65535:
        raise ValueError('tunnel ports must be 1..65535')
    return ['ssh', '-N', '-T', '-n', '-a',
            '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'ConnectTimeout=10', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            '-R', f'127.0.0.1:{tunnel_port}:127.0.0.1:{local_port}', host]


class ReverseTunnel:
    """后台监控 SSH 并重连；状态写日志，不打断终端文本刷新。"""
    def __init__(self, host, local_port, tunnel_port, log_path):
        self.command = ssh_command(host, local_port, tunnel_port)
        self.log_path = log_path
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.thread = None

    def start(self):
        """开启监控线程，将 SSH 启动和连接错误写入日志，不阻塞识别主循环。"""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, daemon=True, name='reverse-tunnel')
        self.thread.start()

    def _run(self):
        """连接中断后退避重试；停止信号打断等待，避免退出时再次连接。"""
        delay = 1
        with self.log_path.open('a', encoding='utf-8') as log:
            while not self.stopping.is_set():
                try:
                    with self.lock:
                        if self.stopping.is_set():
                            break
                        self.process = subprocess.Popen(self.command, stdout=log, stderr=log)
                    code = self.process.wait()
                    log.write(f'SSH exited ({code}); retry in {delay}s\n')
                    log.flush()
                except OSError as exc:
                    log.write(f'SSH startup failed: {exc}\n')
                    log.flush()
                if self.stopping.wait(delay):
                    break
                delay = min(delay * 2, 30)

    def close(self):
        """终止本脚本自己的 SSH 子进程，不影响用户其他 SSH 会话。"""
        self.stopping.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        if self.thread:
            self.thread.join(timeout=4)
