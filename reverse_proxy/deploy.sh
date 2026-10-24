#!/usr/bin/env bash
# 从本机一键部署：复用 SSH 配置和密钥，不上传识别模型或音频。
set -euo pipefail
if (( $# < 2 || $# > 3 )); then
  echo '用法：bash reverse_proxy/deploy.sh SSH主机 域名 [内部隧道端口]' >&2
  exit 2
fi
remote=$1
domain=$2
tunnel_port=${3:-18765}
if [[ ! "$remote" =~ ^[a-zA-Z0-9_@.:-]+$ ]] || [[ "$remote" == -* ]]; then
  echo 'SSH 主机只能是别名、IP 或 user@host。' >&2; exit 2
fi
# 先本地渲染校验参数，避免无效域名进入远端 shell。
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$script_dir/install.sh" "$domain" "$tunnel_port" --print-config >/dev/null
tunnel_port=$((10#$tunnel_port))
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# 安装脚本先上传到专用临时目录；sudo 密码通过独立的远端终端读取。
# 不使用 sudo -S，避免密码与脚本文本混入同一输入流或被记录到日志。
remote_dir=$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 "$remote" \
  'umask 077; mktemp -d /tmp/live-translate.XXXXXXXXXX')
if [[ ! "$remote_dir" =~ ^/tmp/live-translate\.[a-zA-Z0-9]+$ ]]; then
  echo '远端临时目录格式异常，停止部署。' >&2; exit 1
fi
cleanup() {
  # 只删除本次创建的固定文件和空目录，不递归删除远端路径。
  ssh -T -o BatchMode=yes -o ConnectTimeout=10 "$remote" \
    "rm -f '$remote_dir/install.sh'; rmdir '$remote_dir'" </dev/null || true
}
trap cleanup EXIT
ssh -T -o BatchMode=yes -o ConnectTimeout=10 "$remote" \
  "cat > '$remote_dir/install.sh'" < "$script_dir/install.sh"

# 交互执行时分配 PTY，使 sudo 正常显示密码提示；无人值守仍需免密 sudo。
tty_option=-T
if [[ -t 0 ]]; then tty_option=-t; fi
install_command="bash '$remote_dir/install.sh' '$domain' $tunnel_port"
remote_command='if [ "$(id -u)" -eq 0 ]; then '
remote_command+="$install_command; else sudo $install_command; fi"
ssh "$tty_option" -o BatchMode=yes -o ConnectTimeout=10 "$remote" "$remote_command"
