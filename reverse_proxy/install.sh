#!/usr/bin/env bash
# 在远端执行：安装 Nginx 并配置 HTTPS/SSE 代理，不安装 Python、Whisper 或模型。
set -euo pipefail

domain=${1:-}
tunnel_port=${2:-18765}
mode=${3:-install}
# 域名会写入配置及证书路径；只允许标准 DNS 主机名，拒绝注入和下划线。
if (( ${#domain} > 253 )) || [[ "$domain" != *.* ]] || [[ "$domain" =~ [^a-zA-Z0-9.-] ]]; then
  echo '请输入有效域名，例如 live-translate.example.com。' >&2; exit 2
fi
IFS=. read -ra labels <<< "$domain"
for label in "${labels[@]}"; do
  if (( ${#label} < 1 || ${#label} > 63 )) || [[ "$label" == -* || "$label" == *- ]]; then
    echo '域名标签无效。' >&2; exit 2
  fi
done
if [[ "$domain" == *. ]] || ! [[ "$tunnel_port" =~ ^[0-9]+$ ]] || (( 10#$tunnel_port < 1 || 10#$tunnel_port > 65535 )); then
  echo '域名或隧道端口无效。' >&2; exit 2
fi
tunnel_port=$((10#$tunnel_port))
if (( tunnel_port == 80 || tunnel_port == 443 )); then
  echo '隧道端口不能占用 HTTPS 入口的 80/443。' >&2; exit 2
fi

render_config() {
  cat <<CONFIG
# Managed by live_translate reverse_proxy/install.sh
server {
    listen 80;
    server_name ${domain};
    return 301 https://${domain}\$request_uri;
}
server {
    listen 443 ssl;
    server_name ${domain};
    ssl_certificate /root/cert/${domain}/fullchain.pem;
    ssl_certificate_key /root/cert/${domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    error_log /var/log/nginx/live-translate-error.log warn;

    location / {
        proxy_pass http://127.0.0.1:${tunnel_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
CONFIG
}
if [[ "$mode" == --print-config ]]; then
  render_config
  exit 0
fi
if [[ "$mode" != install ]]; then
  echo '用法：install.sh 域名 [内部隧道端口] [--print-config]' >&2
  exit 2
fi
if (( EUID != 0 )); then
  echo '远端安装需要 root 或免交互 sudo。' >&2
  exit 1
fi

# 证书由现有 s-ui 管理；缺少证书时不替换正在工作的旧配置。
for file in fullchain.pem privkey.pem; do
  if [[ ! -s "/root/cert/$domain/$file" ]]; then
    echo "请先申请证书：/root/cert/$domain/$file" >&2; exit 1
  fi
done

# Ubuntu 12.04 可能只有 lsb-release；不假设存在 systemd 或 os-release。
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
elif [[ -r /etc/lsb-release ]]; then
  . /etc/lsb-release
  ID=${DISTRIB_ID,,}
  VERSION_ID=${DISTRIB_RELEASE:-unknown}
fi
case "${ID:-unknown}" in
  ubuntu|debian) ;;
  *) echo '部署脚本仅支持 Debian/Ubuntu。' >&2; exit 1 ;;
esac
echo "服务器系统：${ID} ${VERSION_ID:-unknown}"
if [[ "$ID" == ubuntu && "${VERSION_ID:-}" == 12.* ]]; then
  echo '检测到 Ubuntu 12：使用旧版服务管理方式；软件包安装依赖现有 apt 源仍可用。' >&2
fi
if ! command -v nginx >/dev/null 2>&1; then
  # apt 源失效时直接失败，不自动修改源、不关闭签名或证书验证。
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
fi

config=/etc/nginx/conf.d/live-translate.conf
mkdir -p /etc/nginx/conf.d
backup=$(mktemp)
staged=$(mktemp /etc/nginx/conf.d/.live-translate.XXXXXX)
had_config=0
if [[ -f "$config" ]]; then
  if ! grep -q '^# Managed by live_translate reverse_proxy/install.sh$' "$config"; then
    echo "拒绝覆盖非本项目配置：$config" >&2
    rm -f "$backup" "$staged"
    exit 1
  fi
  cp -p "$config" "$backup"
  had_config=1
fi
restore=1
cleanup() {
  # 配置检测或服务操作失败时，只还原本项目文件，不触碰其他站点。
  if (( restore )); then
    if (( had_config )); then cp -p "$backup" "$config"; else rm -f "$config"; fi
  fi
  rm -f "$backup" "$staged"
}
trap cleanup EXIT
render_config > "$staged"
chmod 644 "$staged"
mv "$staged" "$config"
nginx -t
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  systemctl enable nginx
  if systemctl is-active --quiet nginx; then systemctl reload nginx; else systemctl start nginx; fi
else
  if service nginx status >/dev/null 2>&1; then service nginx reload; else service nginx start; fi
fi
restore=0

echo "部署完成：浏览器访问 https://${domain}/"
echo "本机运行：python3 live_translate.py --proxy-host remote-host --proxy-tunnel-port ${tunnel_port} --events"
echo "仅需公网开放 TCP 80/443，隧道 ${tunnel_port} 保持回环监听；本机 HTTP 端口仍为 8765。"
echo "旧版受管配置已替换，不再监听公网 8765；请同步移除旧防火墙规则。"
