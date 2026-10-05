# Agent Computer 中文上手与演示

想让 ChatGPT 也像 Muse 那样操作电脑？Agent Computer 通过 MCP，把一台
Linux 电脑上的桌面、Chromium、终端和文件接入现有 AI 客户端。你能看到
操作过程，下载结果，也能接管。模型和任务推进由客户端负责；本服务没有
内置大模型循环，不会在客户端停止调用后自行持续思考。

## 本地启动

需要 Docker Compose、Python 3，建议留出 3 GB 内存。首轮真实验收平台是
Linux x86-64；其他平台需要自行验证 Docker 支持，不能把 CI 当成全平台验证。

```sh
git clone https://github.com/connbot/desktop-bridge.git
cd desktop-bridge
python3 scripts/setup.py
docker compose up --build -d
```

首次构建要下载浏览器和中文字体。打开 http://localhost:8080，用本地 .env 中的
BRIDGE_OWNER_TOKEN 登录。不要把这个管理令牌发给模型，也不要提交 .env。

## 接入 AI 客户端

MCP 地址为 http://localhost:8080/mcp。实现 Streamable HTTP、OAuth 授权码、
S256 PKCE 和动态客户端注册。连接时会跳转到你的桌面服务，显示客户端名称、
回调地址和权限，由你明确批准。一小时后重新授权。

云端 ChatGPT/Claude 无法访问你电脑的 localhost，需要自己已有的 HTTPS 域名
和可达服务器/隧道。将 BRIDGE_PUBLIC_URL 改成实际 HTTPS origin，重启容器，
通过反向代理转发 HTTP 和 WebSocket。只转发网关 8080，别公开 5900、5901、9222，
更不要挂载 Docker socket。仓库有 Caddy 示例，但不会替你申请账号、购买算力或
把 GitHub Actions 当作持续托管服务器。

已验证官方 MCP SDK 协议调用和 Docker 真桌面集成；项目作者还在
2026 年 10 月 4 日确认其 ChatGPT 账号已成功连接并调用工具。这是特定账号的
测试结果，不代表所有账号或平台都支持。ChatGPT 需要自定义 MCP 写入权限，
请核对 [OpenAI 当前账号要求](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)。
Claude 账号接入、各客户端审批行为及你的域名连通性仍需单独确认。

## 五分钟演示

1. 本地启动并登录观看页，连接支持 OAuth 的 MCP 客户端。
2. 让 AI 调用 session_start，然后 browser_action 打开
   http://127.0.0.1:8080/static/demo.html（这是容器内的地址）。
3. 让 AI 查看 browser_snapshot，点“Project note”，再用 desktop_action 的 type
   输入中文，通过页面按钮保存。展示 GUI 与结构化操作使用同一台电脑。
4. 让 AI 用 coding_apply_patch 创建一个文件，再用 coding_read_file 读取验证。
   观看页点 Refresh 后下载产物。
5. 点击 Take control，确认 AI 写入被拒绝；Private takeover 还会暂停模型看屏。
   点击 Hand back to AI 后，让 AI 重新截图再操作。
6. 点击 Disconnect & revoke，旧 MCP 令牌立即失效。

模型每次写入应使用新的 action_id 或 bridge_action_id；同一动作重试必须保留原 ID。
OUTCOME_UNKNOWN 表示外部动作可能已完成，不能机械重放。

## 常见问题

- 空白画面：点 Reconnect screen，查看 docker compose logs。看屏连接要完成后才有画面。
- AI 被拒绝：检查是否处于 HUMAN、PRIVATE、PAUSED 或 STOPPED，手动交还控制。
- STALE_OBSERVATION：重新截图/浏览器快照，不要复用旧坐标。
- 中文输入：type 使用本地 UTF-8 剪贴板再粘贴，支持多行；终端应用可能需要自己的粘贴快捷键。
- 重启：文件与浏览器 profile 保存在卷中，登录授权失效需要重新批准。运行中进程不是内存快照。
- 关闭电脑：docker compose stop；恢复用 docker compose start。
- 删除数据：docker compose down 不删除卷；不要随意加 -v，那会删除工作区和 profile。

## 安全边界

这是单一可信用户的自托管电脑。任意 Shell 可影响整个容器，不能作为多租户强隔离。
接管会阻止新动作、等待在途动作，并终止跟踪到的 Shell 进程；已经发生的外部提交、
脱离管理的后台进程不能保证撤销。不要在未经检查的会话里登录高价值账户。
默认仅绑定本机地址。浏览器运行在非 root 容器中，Chromium 的内部 sandbox 未启用。
