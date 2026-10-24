# Live Translate

将 Mac 正在播放的英文音频实时转写、翻译为中文，并同步到终端和局域网网页。网页可使用客户端语音朗读稳定译文。

`系统音频 → BlackHole → whisper-stream → 分句与修订 → Google 翻译 → 终端 / Web → 中文朗读`

## 文档导航

| 文档 | 内容 |
|---|---|
| [安装与音频采集](whisper.md) | 环境准备、编译 Whisper、下载模型、配置 BlackHole、验证采集 |
| [远端反向代理（可选）](reverse_proxy/README.md) | 通过 SSH 隧道从远端访问 |
| [运行与参数](live_translate.md) | 启动命令、模型选择、输出文件、网页语音、同步规则、测试与排错 |

## 开始使用

首次使用请先完成[安装与音频采集](whisper.md)。以下命令均在仓库根目录执行。

```bash
# 检查环境、真实识别、翻译接口及服务逻辑
python3 test_live_translate.py

# 启动实时翻译、事件日志和网页服务
python3 live_translate.py --events --web
```

默认使用 `models/ggml-tiny.en.bin`、CPU 9 线程、采集设备 #2。BlackHole 编号不同可添加 `--capture 编号`；具体查询方法见[采集设备检查](whisper.md#检查实时采集)。

本机打开 `http://127.0.0.1:8765`，局域网其他设备打开 `http://Mac的局域网IP:8765`。在网页选择中文声音，点击“开启实时朗读”。历史内容只显示，不会自动播放。

## 命令示例

以下示例任选一条运行。默认使用 tiny.en、CPU 9 线程、采集设备 #2；稳定结果追加到 `output.txt`，Whisper 诊断写入 `whisper.log`。`--events` 额外开启事件日志，不加则不记录。

### 仅终端

```bash
# 英文识别 + 中文翻译，保存稳定结果
python3 live_translate.py

# 只识别英文，不调用翻译
python3 live_translate.py --no-translate

# 终端翻译，不保存结果文件（仍有 Whisper 诊断日志）
python3 live_translate.py --no-output
```

### 网页与朗读

```bash
# 本机及局域网网页，保存稳定结果
python3 live_translate.py --web

# 网页 + 事件日志，便于排查文本修订和翻译问题
python3 live_translate.py --web --events

# 网页仅允许本机访问
python3 live_translate.py --web --host 127.0.0.1
```

本机访问 `http://127.0.0.1:8765`；局域网访问 `http://Mac的局域网IP:8765`。中文朗读需在网页中手动开启。`--no-translate` 不能与网页或远端访问同时使用。

### 模型与输出

```bash
# 使用 base.en，通常准确率更高、耗时更多；需已下载模型
python3 live_translate.py -m models/ggml-base.en.bin --capture 2 --threads 9 --web

# 自定义结果和日志路径
python3 live_translate.py --web --output result.txt --events session.jsonl --log whisper-session.log
```

`--capture` 填写实际 BlackHole 编号，`--threads` 控制 Whisper 计算线程数。

### 服务器反向代理（可选）

本机和局域网使用无需反代。已有远端反代入口时执行：

```bash
# 远端访问，自动启用网页，保存稳定结果
python3 live_translate.py --proxy-host 你的IP

# 远端访问 + 事件日志
python3 live_translate.py --proxy-host 你的IP --events
```

浏览器访问 `https://你的反代域名/`。`你的IP` 可替换为 SSH 别名或 `用户@服务器IP`。仅需远端访问、不开放本机局域网入口时，可加 `--host 127.0.0.1`。

### 参数示例

精简版：

```bash
python3 live_translate.py \
  --model models/ggml-base.en.bin \
  --capture 2 \
  --threads 9 \
  --web \
  --port 8765 \
  --proxy-host 你的IP \
  --proxy-tunnel-port 18765
```

完整版，自定义识别、翻译、局域网网页、远端访问及日志：

```bash
python3 live_translate.py \
  --binary bin/whisper-stream \
  --model models/ggml-base.en.bin \
  --capture 2 \
  --threads 9 \
  --web \
  --host 0.0.0.0 \
  --port 8765 \
  --proxy-host 你的IP \
  --proxy-tunnel-port 18765 \
  --proxy-log proxy.log \
  --output output.txt \
  --events log.jsonl \
  --log whisper.log \
  --translation-workers 4 \
  --batch-wait-ms 50
```

仅本机和局域网使用时，删除三个 `--proxy-*` 参数即可。更多说明见[运行与参数](live_translate.md)和[远端访问指南](reverse_proxy/README.md)。

## 主要功能

- 英文原文与中文译文对应显示，识别修订时原位更新。
- 未稳定内容同样翻译；新译文到达前保留上一版译文。
- 异步批量翻译，按文本版本丢弃过期结果。
- 最终稳定内容实时追加到 `output.txt`，每句一行英文、一行中文。
- 网页仅展示本次服务运行的内容，支持增量同步、断线重连和会话重置。
- 网页语音按大陆中文、台湾中文分组，支持倍速、音量和 Cookie 设置保存。

## 文件结构

| 路径 | 用途 |
|---|---|
| `live_translate.py` | Python 服务和终端入口，仅使用标准库 |
| `live-web/` | 网页、图标和图标许可证，需随服务一起部署 |
| `test_live_translate.py` | Python 测试及真实环境检查入口 |
| `test_live_web.cjs` | Node 前端逻辑测试 |
| `samples/1.wav` | 识别测试样本，已纳入仓库 |
| `bin/`、`models/`、`whisper.cpp/` | 本地编译程序、模型和依赖源码，不纳入仓库 |

## 使用边界

音频采集指南针对 macOS 和 BlackHole。当前流程识别英文、翻译为中文；网页使用 Web Speech API，不需要安装 chrome-tts 扩展，也未接入 Edge TTS。Google 翻译需要网络，英文文本会发送至 Google。

HTTP 服务默认监听 `0.0.0.0:8765`，无鉴权，适合可信局域网。仅本机使用时添加 `--host 127.0.0.1`。

同机朗读可能回流到 BlackHole。建议在另一台设备打开网页播放，或另行隔离音频路由。识别错误、专有名词和译文仍需人工校对。
