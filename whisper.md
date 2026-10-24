## 原理
Mac音频 -> 实时提取文本 -> 翻译(google) -> 终端实时显示+翻译 -> Web实时显示+播放(Chrome-TTS)

## whisper-cli安装及测试:

1. 编译（CPU静态版本，服务器用，实测纯CPU比AMD5600XT更快）

```shell
# 必要工具安装
xcode-select --install
brew install cmake sdl2
brew install python node
brew install --cask blackhole-2ch
# 编译whisper-cpp
git clone https://github.com/ManyyWu/live_translate.git
cd live_translate
git clone https://github.com/ggml-org/whisper.cpp.git && cd whisper.cpp
cmake -S . -B build-cpu-static \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_METAL=OFF \
  -DGGML_VULKAN=OFF \
  -DWHISPER_SDL2=ON
cmake --build build-cpu-static --config Release -j 20
```

2. 安装

```shell
cd .. && mkdir -p models bin
cp whisper.cpp/build-cpu-static/bin/{whisper-stream,whisper-cli} ./bin
cp -p whisper.cpp/models/download-ggml-model.sh ./models
```

3. 下载模型
```shell
sh models/download-ggml-model.sh base.en 
sh models/download-ggml-model.sh tiny.en
```

4. 测试
```shell
./bin/whisper-cli -ng -t 9 -m models/ggml-base.en.bin -l en -f samples/1.wav -otxt -of output-base
./bin/whisper-cli -ng -t 9 -m models/ggml-tiny.en.bin -l en -f samples/1.wav -otxt -of output-tiny
```

## 实时语音转文本采集测试

1. 系统音频采集
```shell
brew install blackhole-2ch
```
重启后打开“音频MIDI设置”
  1. 打开“音频 MIDI 设置”，确认出现 BlackHole 2ch。
  2. 点击左下角 + →“创建多输出设备”。
  3. 勾选物理输出和 BlackHole 2ch，物理输出作为主设备，给 BlackHole 开启“漂移校正”。
  4. 将该多输出设备设为系统声音输出

2. 获取 BlackHole 2ch 的设备编号
```shell
./bin/whisper-stream -ng -m /dev/null 2>&1 | grep 'Capture device #.*BlackHole 2ch'
# init:    - Capture device #2: 'BlackHole 2ch'
# #2 即编号
```

3. 测试采集
```shell
./bin/whisper-stream -ng -t 9 -m models/ggml-tiny.en.bin -l en -c 2
```

# 一体化语音同传服务
```shell
# 先运行一次测试
python3 test_live_translate.py
# 启动服务
python3 live_translate.py -m models/ggml-tiny.en.bin --events --web
```

# Web页面

http://127.0.0.1:8765/

http://Mac的局域网IP:8765/