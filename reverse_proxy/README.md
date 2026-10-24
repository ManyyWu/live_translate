# 远端访问（可选）

[返回项目首页](../README.md) · [本机服务参数](../live_translate.md)

本机和局域网使用无需反代。以下用法适用于已有远端 HTTPS 反代入口的情况。

## 启动

在项目根目录执行：

```bash
python3 live_translate.py --proxy-host 你的IP --events
```

`你的IP` 替换为服务器 IP，也可使用本机 SSH 别名或 `用户@服务器IP`。SSH 使用本机现有密钥和主机指纹配置，需要可免交互登录。

浏览器访问 `https://你的反代域名/`，无需路径前缀。网页语音在访问页面的设备上播放。

## 参数

| 参数 | 作用 | 默认值 |
|---|---|---|
| `--proxy-host` | SSH 目标，自动启用网页及反向隧道 | 不启用 |
| `--proxy-tunnel-port` | 远端回环隧道端口 | `18765` |
| `--proxy-log` | SSH 日志，追加写入 | `proxy.log` |
| `--host` | 本机 HTTP 监听地址 | `0.0.0.0` |
| `--port` | 本机 HTTP 端口 | `8765` |

域名无需传入本机程序。断线后隧道自动重连；Ctrl+C 同时关闭识别和本程序创建的 SSH 隧道，不影响其他 SSH 会话。

## 文件与测试

`deploy.sh`、`install.sh` 是可选部署工具，日常启动无需执行；`tunnel.py` 负责本机隧道启动、重连和清理。运行代理功能时需保留 `reverse_proxy/` 目录。

```bash
python3 -m unittest reverse_proxy.test_reverse_proxy -v
```

测试使用模拟 SSH 和配置渲染，不连接或部署真实服务器。
