# Live Translate

将 Mac 正在播放的英文音频实时转写、翻译为中文，并同步到终端和局域网网页。网页可使用客户端语音朗读稳定译文。

`系统音频 → BlackHole → whisper-stream → 分句与修订 → Google 翻译 → 终端 / Web → 中文朗读`

## 文档导航

| 文档 | 内容 |
|---|---|
| [安装与音频采集](whisper.md) | 环境准备、编译 Whisper、下载模型、配置 BlackHole、验证采集 |
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
