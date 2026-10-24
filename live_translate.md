# live_translate.py 使用帮助

基本运行，默认 tiny.en、CPU 9 线程、BlackHole #2：

```bash
python3 live_translate.py
```

选择模型并记录事件日志：

```bash
python3 live_translate.py -m models/ggml-base.en.bin --events
```

| 参数 | 作用 | 默认值 |
|---|---|---|
| `-m` / `--model 路径` | 模型文件 | 脚本目录下的 `models/ggml-tiny.en.bin` |
| `--capture 编号` | SDL 音频输入设备编号 | `2` |
| `--threads 数量` | Whisper 计算线程数 | `9` |
| `--binary 路径` | whisper-stream 程序 | 脚本目录下的 `bin/whisper-stream` |
| `--output 路径` | 最终稳定中英文本，追加写入 | 脚本目录下的 `output.txt` |
| `--no-output` | 不生成文本文件 | — |
| `--events [路径]` | 开启 JSONL 事件日志，覆盖写入 | 开启且不指定路径时为脚本目录下的 `log.jsonl` |
| `--log 路径` | Whisper 诊断日志，覆盖写入 | 脚本目录下的 `whisper.log` |
| `--no-translate` | 只识别英文，不调用翻译 | — |
| `--translation-workers 数量` | 并发翻译请求数，1～16 | `4` |
| `--batch-wait-ms 毫秒` | 翻译合批等待，0～1000 | `50` |
| `--replay 路径` | 回放原始 stdout 记录，不采集音频 | — |
| `--web` | 启用 HTTP 网页客户端 | 默认关闭 |
| `--host 地址` | HTTP 监听地址 | `0.0.0.0` |
| `--port 端口` | HTTP 端口 | `8765` |
| `-h` / `--help` | 显示帮助 | — |

自定义输出文件：

```bash
python3 live_translate.py -m models/ggml-tiny.en.bin --capture 2 --output result.txt --events log.jsonl
```

显式指定的相对路径以当前终端目录为起点。默认路径以脚本所在目录为起点。

## 使用前准备

系统声音输出应设为包含 BlackHole 和耳机/扬声器的多输出设备，并允许当前终端访问麦克风。设备编号可能变化，可运行以下命令查询 BlackHole 编号，再传给 `--capture`：

```bash
./bin/whisper-stream -ng -m /dev/null 2>&1 | grep 'Capture device #.*BlackHole 2ch'
```

脚本自动启动 whisper-stream，使用 CPU、英文识别、默认 3 秒更新和 10 秒窗口，不启用 `-kc`。脚本不修改系统音频路由。

## 显示与文件

终端每句显示两行：英文在上、中文在下，当前识别块可以修订。英文更新时保留上一版译文，直到新译文返回；从未获得译文的新句子显示“翻译中”。长行会根据终端宽度自动折行。

`output.txt` 每次运行先追加时间、脚本启动命令和实际 Whisper 命令，正文只写已固定的完整英文及当前版本译文，每句两行。不写中间修订、旧译文、占位提示或停止时未稳定的末句。翻译失败的句子不写入正文；`--no-translate` 模式只写稳定英文。

`log.jsonl` 是可选输出日志，每行一个 JSON，记录文本修订、请求包、译文更新、过期结果丢弃及错误详情。`whisper.log` 保存 Whisper 初始化和运行诊断。

终端支持 ANSI 控制序列时才能原位刷新；重定向 stdout 时只输出固定结果。

## 翻译与停止

翻译使用 Google 网页接口，无需 API Key，英文文本会发送至 Google。默认等待 50 ms 合批、4 个并发请求，每包最多 16 条，累计 8000 字符后另开包，单条超长文本独立成包。通过句子版本和请求包 ID 防止旧响应覆盖新内容。单次请求超时为 8 秒，失败不阻塞其他条目。

`Ctrl+C` 停止采集，并等待排队中的翻译结束。`--replay` 接收 Whisper 原始 stdout 记录，不能输入 `log.jsonl`。

## HTTP 网页与本机语音

```bash
python3 live_translate.py -m models/ggml-tiny.en.bin --events --web
```

打开 `http://127.0.0.1:8765`。网页通过 SSE 首次获取本次服务启动后生成的全部文本，之后只发送增量修订；断线重连发送最新全量快照。服务每次启动有独立会话 ID，网页检测到新会话会清空旧序号、文本及播放队列，避免重启后停止同步。不读取 `output.txt` 中的往次记录。网页刷新或重连仍保留本次运行的结果，重启脚本后重新从空列表开始；文本文件继续追加保存。默认监听 `0.0.0.0`，同一局域网设备连接 `http://电脑局域网IP:8765`。如需仅本机访问，使用 `--host 127.0.0.1`。服务为 HTTP，无登录鉴权，应只在可信网络使用。

网页只能使用 Web Speech API，不能直接调用扩展专用的 `chrome.tts`。实现参考 chrome-tts 的本地模式，使用浏览器 `speechSynthesis`，列出浏览器提供的语音，并标注“本地 | 声音名称”或“远程 | 声音名称”，不接入 Edge TTS。选择远程声音时由浏览器调用对应服务。语音选择、0.5～1.5 倍速和 0～100% 音量保存到 Cookie（有效期一年）；设置用于下一句朗读。

声音只保留大陆中文和台湾中文，按 zh-CN、zh-TW 分组排序，zh-CN 带星号；保留同一语言的全部可用声音，不按语言去重。兼容 zh_CN、zh-Hans、zh-Hant 等语言标记。移动版实际可选数量取决于浏览器暴露的系统语音列表，不保证与桌面版相同。文本列表采用紧凑的一行英文、一行中文布局，长句自动折行。页面不整体滚动，仅列表内部滚动；离开底部时停止跟随，到达底部时自动恢复跟随，无需勾选。

连接时不自动朗读历史。点击“开启实时朗读”后，仅将新稳定、拥有当前版本译文且由服务端按原文顺序开放播放的句子加入队列，临时文本和暂留旧译文不播放；重连快照不会重复排队。可点单句“朗读”、朗读全部历史或试听。客户端各自选择自己的本机声音；浏览器须先获得一次用户点击才能可靠播放。停止实时朗读会清空播放队列。切换或刷新网页后需要重新开启。

注意：网页原生语音无法通过此接口单独选择输出声卡。在采集服务同一台 Mac 播放时，语音可能进入系统多输出设备并回流 BlackHole。可在另一台设备打开网页播放，或另用音频路由工具隔离浏览器 TTS 与原始直播音频。

部署时保留 `live_translate.py` 和 `live-web/`（HTML、JS、CSS）。HTTP 服务只暴露网页静态文件、`/api/state` 和 `/api/events`，不提供任意文件浏览或远程命令执行。

## 测试

无参数运行全部自动测试和真实环境检查：

```bash
python3 test_live_translate.py
```

依次执行 Python 单元/HTTP/SSE 测试、文件和执行权限检查、Whisper 可执行程序启动、默认 tiny 模型加载、`samples/1.wav` 前 15 秒真实 CPU 识别、SDL 设备枚举、BlackHole 约 10 秒采集、Google 真实批量翻译，以及 Node 网页逻辑测试。每项显示通过或失败，有失败时退出码为 1，全部通过为 0；单项失败后继续其他检查。

运行需要网络、本地端口权限、终端录音权限和 Node。不会修改系统声音路由，也不改写 `output.txt` 或现有日志；样本识别文件位于临时目录并自动清除。若当前未播放音频，实时采集可能没有语音输出，报告会明确说明，不将静音视为失败。

真实浏览器是否出声、手机声音引擎、TTS 回流隔离和人工识别准确率仍需手动验证，不能用模拟测试代替。

只运行服务逻辑测试（不执行真实 Whisper、BlackHole 和 Google 检查）：

```bash
python3 -m unittest test_live_translate -v
```

只运行网页逻辑测试：

```bash
node --test test_live_web.cjs
```

同步边界：服务端保留最近 256 个增量版本，慢客户端遗漏的版本会合并发送，超出记录范围时退回全量；合并条目按原文顺序排列。未稳定的停止尾句不会开放朗读，也不会写入最终文件。
