# 远端访问（可选）

[返回项目首页](../README.md) · [本机服务参数](../live_translate.md)

本机和局域网使用无需反代。以下用法适用于已有远端 HTTPS 反代入口的情况。

## 配置远端 HTTPS 入口

以下以 Ubuntu/Debian 的 Nginx 目录结构为例，域名统一使用 `live-translate.example.com`，SSH 别名使用 `remote-host`。执行前替换为自己的配置。

连接路径：浏览器 → 远端 Nginx（HTTPS，443）→ 远端回环端口 `18765` → SSH 隧道 → 本机 HTTP 服务（`8765`）。远端不运行 Whisper，也不需要模型。

### 1. 准备域名和证书

将子域名的 DNS A 记录指向服务器公网 IPv4；如果添加 AAAA 记录，也需确保 IPv6 可访问。服务器需要放行 TCP `80`、`443` 以及 SSH 端口；`18765` 只监听回环地址，无需对公网开放。

使用证书工具为该子域名申请证书。下文使用以下示例路径，配置时需替换为证书工具实际生成的路径：

```text
/root/cert/live-translate.example.com/fullchain.pem
/root/cert/live-translate.example.com/privkey.pem
```

如果工具要求独占 80 端口，申请前执行 `sudo systemctl stop nginx`，完成后恢复启动；这期间同一 Nginx 的其他站点也会暂停。使用其他证书工具时，按实际位置修改配置，并确保续期后重载 Nginx。

### 2. 添加 Nginx 配置

在服务器执行；尚未安装 Nginx 时，先执行 `sudo apt update && sudo apt install -y nginx`。

```bash
sudo tee /etc/nginx/sites-available/live-translate.example.com > /dev/null <<'EOF'
server {
    listen 80;
    server_name live-translate.example.com;

    access_log off;
    return 301 https://live-translate.example.com$request_uri;
}

server {
    listen 443 ssl;
    server_name live-translate.example.com;

    ssl_certificate /root/cert/live-translate.example.com/fullchain.pem;
    ssl_certificate_key /root/cert/live-translate.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    access_log off;

    location / {
        proxy_pass http://127.0.0.1:18765;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        # SSE 需要关闭缓冲，避免翻译结果积攒后才发送。
        proxy_buffering off;
        proxy_cache off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
```

### 3. 启用站点

```bash
sudo ln -sfn /etc/nginx/sites-available/live-translate.example.com /etc/nginx/sites-enabled/live-translate.example.com
sudo nginx -t && sudo systemctl reload nginx
```

如果此前已停止 Nginx，将最后一条改为 `sudo nginx -t && sudo systemctl start nginx`；已停止的服务无法执行 reload。

也可在准备好 DNS 和上述证书后，从本机项目目录执行一键部署，替代第 2、3 步：

```bash
bash reverse_proxy/deploy.sh remote-host live-translate.example.com
```

部署脚本使用 `/etc/nginx/conf.d/live-translate.conf`，手动配置使用 `sites-available` 和 `sites-enabled`。两种方式选择一种，避免同一域名重复配置。部署完成会启动或重载远端 Nginx；本机翻译服务仍需按下文单独启动。

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

本机服务或隧道关闭后，新请求返回 `502 Bad Gateway`，无法再获取翻译内容；重新启动并建立隧道后恢复访问。浏览器已经接收的内容仍可能留在页面中。HTTPS 提供传输加密，当前配置没有访问认证，服务在线时知道网址的人即可访问。

## 文件与测试

`deploy.sh`、`install.sh` 是可选部署工具，日常启动无需执行；`tunnel.py` 负责本机隧道启动、重连和清理。运行代理功能时需保留 `reverse_proxy/` 目录。

```bash
python3 -m unittest reverse_proxy.test_reverse_proxy -v
```

测试使用模拟 SSH 和配置渲染，不连接或部署真实服务器。
