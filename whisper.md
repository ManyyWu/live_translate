# Whisper 安装与系统音频采集

[返回 README](README.md) · [运行与参数](live_translate.md)

本指南用于 macOS：编译 CPU 版本的 Whisper，使用 BlackHole 将系统音频送入识别服务。完成后按[运行与参数](live_translate.md#启动服务)启动翻译。

## 1. 准备环境

需要已安装 Homebrew。已存在的工具可以跳过安装；Xcode 命令行工具需要在系统弹窗中完成安装后再继续。

```bash
xcode-select --install
brew install cmake sdl2 python node
brew install --cask blackhole-2ch
```

BlackHole 安装完成后**重启 Mac**。Python 服务仅使用标准库，无需安装 pip 包；Node 用于前端测试。

验证工具：

```bash
xcode-select -p
cmake --version
sdl2-config --version
python3 --version
node --version
```

## 2. 获取项目并编译 Whisper

```bash
git clone https://github.com/ManyyWu/live_translate.git
cd live_translate
git clone https://github.com/ggml-org/whisper.cpp.git

cmake -S whisper.cpp -B whisper.cpp/build-cpu-static \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_METAL=OFF \
  -DGGML_VULKAN=OFF \
  -DWHISPER_SDL2=ON

cmake --build whisper.cpp/build-cpu-static --config Release -j 8
```

**此后所有命令都在 `live_translate` 仓库根目录执行。** `WHISPER_SDL2=ON` 用于生成实时采集程序；关闭 Metal 和 Vulkan 后不需要 Vulkan SDK。`-j 8` 是编译并行数，与运行时识别线程数无关，可按机器调整。

这里选择 CPU 是因为项目在当前 Intel Mac、RX 5600 XT 和 base.en 的测试中，CPU 快于 Vulkan/MoltenVK；其他硬件和模型需另行测试。

## 3. 安装程序和下载模型

```bash
mkdir -p models bin
cp whisper.cpp/build-cpu-static/bin/whisper-stream bin/
cp whisper.cpp/build-cpu-static/bin/whisper-cli bin/
cp whisper.cpp/models/download-ggml-model.sh models/

# 默认模型：优先低延迟
sh models/download-ggml-model.sh tiny.en

# 可选模型：用于准确率对比
sh models/download-ggml-model.sh base.en
```

下载参数是模型名 `tiny.en`，不要写成 `models/tiny.en`。模型文件会保存在下载脚本所在的 `models/` 目录中。

`bin/`、`models/` 和 `whisper.cpp/` 已被 Git 忽略，因此在新机器上需要重新执行编译、复制和下载步骤。

## 4. 验证文件识别

```bash
./bin/whisper-cli -ng -t 9 -m models/ggml-tiny.en.bin \
  -l en -f samples/1.wav -otxt -of output-tiny

./bin/whisper-cli -ng -t 9 -m models/ggml-base.en.bin \
  -l en -f samples/1.wav -otxt -of output-base
```

分别生成 `output-tiny.txt`、`output-base.txt`。第二条需要已下载 base.en。`-ng` 使用 CPU，`-t 9` 指定计算线程数，不等于进程的全部线程数。

## 5. 配置 BlackHole

1. 打开 macOS“音频 MIDI 设置”，确认存在 **BlackHole 2ch**。
2. 点击左下角 `+`，创建“多输出设备”。
3. 勾选实际使用的耳机/扬声器输出，以及 BlackHole 2ch。
4. 将物理输出设为主设备，为 BlackHole 开启“漂移校正”，两者采样率保持一致，例如 48 kHz。
5. 在系统声音设置中将输出选为该多输出设备。
6. 在“隐私与安全性 → 麦克风”中允许运行脚本的终端访问录音设备。

普通模拟耳机使用“内建输出”；只有实际连接数字音频设备时才选“内建数码输出”。多输出设备通常无法直接使用系统音量键，可通过播放器或物理设备调节。播放器静音也会让 BlackHole 无法收到语音。

## 检查实时采集

先查询设备编号：

```bash
./bin/whisper-stream -ng -m /dev/null 2>&1 | grep 'Capture device #.*BlackHole 2ch'
```

例如输出 `Capture device #2: 'BlackHole 2ch'`，就使用 `-c 2`。这是设备列表编号，与日志中的 `SDL Id` 句柄不同。查询命令用无效模型提前退出，只用于列出设备。

保持英文视频正在播放，再运行：

```bash
./bin/whisper-stream -ng -t 9 -m models/ggml-tiny.en.bin -l en -c 2
```

如果实际编号不是 2，请替换 `-c` 参数。启动日志应明确显示打开 BlackHole；默认每 3 秒更新、音频窗口 10 秒，不启用 `-kc`。按 `Ctrl+C` 停止。

## 6. 检查并启动翻译服务

```bash
python3 test_live_translate.py
python3 live_translate.py --capture 2 --events --web
```

测试会短暂采集 BlackHole、请求 Google，并验证本机 HTTP 服务。默认使用 tiny.en，切换模型、访问网页和查看日志请继续阅读[运行与参数](live_translate.md)。

本机访问 `http://127.0.0.1:8765`；其他设备访问 `http://Mac的局域网IP:8765`，不能使用手机自己的 `127.0.0.1`。
