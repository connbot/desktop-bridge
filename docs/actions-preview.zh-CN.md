# GitHub Actions → Cloudflare Tunnel → ChatGPT

这是 Agent Computer 的按需开发/测试入口：Actions 启动真实 Docker 桌面，
Cloudflare 提供 HTTPS 地址，ChatGPT 通过 OAuth + Streamable HTTP 调用桌面、
浏览器、文件和终端工具。无需 OpenAI API key；模型由你的 ChatGPT 客户端提供。

## 最短路径：临时地址

1. 在 GitHub 仓库 Settings → Secrets and variables → Actions 新建 repository secret：
   `BRIDGE_OWNER_TOKEN`。值支持普通密码或口令，8–256 个字符，允许空格和符号（不能全是空白）。
   建议使用独立的随机密码，不要复用其他账号的密码。只在 GitHub 的 secret 输入框和本服务的登录页输入，不要发到聊天、
   workflow inputs、Issue、代码或日志。它允许登录并批准电脑访问。
2. 打开 Actions → **Launch MCP preview** → Run workflow。
   `mode=preview`，`tunnel=quick`，默认运行 60 分钟。可以选 30/120/350 分钟。
   `public_url` 留空。不需要 Cloudflare 账号或 Cloudflare token。
3. 等待构建、隧道就绪和真实公网烟测。展开 **Start authenticated HTTPS MCP preview**
   步骤或打开运行 Summary，复制 `MCP endpoint`，形如
   `https://随机名称.trycloudflare.com/mcp`。验证模式不会留下可用会话。
4. 在支持自定义 MCP 的 ChatGPT/OpenAI 界面创建连接，填完整 `/mcp` 地址；
   认证选择 **OAuth**，客户端注册选择 **Dynamic client registration / DCR**（若界面询问）。
   不需要静态 client ID 或 client secret。不要选择“无认证”。
5. 跟随授权页，用第一步的 owner token 登录；核对客户端及回调地址，再批准访问。
   授权页说明客户端能查看和操作桌面、浏览器、文件和终端。
6. 在聊天中选择这个连接，先试：“查看电脑当前状态，再截一张图；不要登录任何账号。”
   也可以让它写一个测试文件并读取回来。
7. Summary 中的 `Desktop and OAuth login` 地址可查看实时桌面、接管、暂停或下载文件。
   如果 AI 被暂停，点击 **Hand back to AI**；AI 不能自行撤销你的暂停或隐私接管。

ChatGPT 中是否显示创建连接入口取决于账户、工作区权限和当前产品界面。
按照 [OpenAI 接入文档](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
完成连接。协议烟测不等于已经替你完成 ChatGPT 账户内的连接验证。

## 固定地址：Named Tunnel（可选）

如果不想每次重填随机地址，使用你自己 Cloudflare 账号中的专用 Named Tunnel：

1. 在 Cloudflare 中预先配置一个自有域名的 public hostname，将服务指向
   `http://127.0.0.1:8080`，保留原始 Host；不要暴露 VNC/CDP 端口。
2. 将该 tunnel 的 token 保存为仓库 Actions secret `CLOUDFLARE_TUNNEL_TOKEN`。
   不要把它写进 workflow 文件或公开输入。此 token 允许运行该隧道。
3. Run workflow 选择 `tunnel=named`，`public_url=https://你的域名`（不要加 `/mcp`）。
4. ChatGPT 使用 `https://你的域名/mcp`。必须是已配置指向本次 runner 的专用 tunnel，
   不要混用正在服务其他机器的 tunnel，也不要让两个预览同时运行。

固定域名不代表永久在线。OAuth 注册和授权保存在内存中：服务重启后需要重新授权；
如果 ChatGPT 复用旧 client ID 而提示 unknown/unregistered client，删除并重新创建该连接。
本版访问 token 有效期一小时，没有 refresh token；过期后重新连接授权。
不要在 MCP 路由前加交互式 Cloudflare Access 邮件验证码，它会阻挡 ChatGPT 的服务端请求。

## 生命周期和边界

- 这套 workflow 用来开发、测试和演示 Agent Computer；不循环自启，
  不充当长期生产托管服务。遵守 [GitHub Actions 使用条款](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)。
- GitHub-hosted job 最长 6 小时；本 workflow 最长 360 分钟，互动时长最多 350 分钟，
  为构建、验证和清理留出余量。取消 workflow 会关闭隧道和电脑。
- Quick Tunnel 每次地址不同、没有可用性保证、最多 200 个并发请求且不支持 SSE。
  本服务的 Streamable HTTP POST 返回 JSON，不依赖 SSE；实时桌面使用 WebSocket。
- runner 是一次性的。结束、取消或重跑后文件、浏览器登录状态和收据不会保留。
  需要的文件在结束前从工作台下载。不会将工作区或浏览器 profile 上传到公开 Actions artifacts。
- 服务有 OAuth 鉴权。知道 URL 并不等于有电脑控制权。owner token 不接受直接 MCP Bearer 调用。
- 这是可信单用户环境，不能隔离恶意 shell/多租户。不要登录高价值账号、上传敏感资料或执行不可信代码。
- 此 preview 不把 GitHub token、Cloudflare token、Docker socket 或 runner 文件系统挂进桌面容器。
  Cloudflare 会代理这次连接的请求；GitHub 承载临时桌面。构建分钟数和资源配额仍取决于 GitHub 账户。

## 验证模式

`mode=verify, tunnel=quick` 使用一次性的随机测试凭证，跑完就销毁，不需要配置 owner secret。
只用于测试，不打印或交付登录凭证。main 上修改 preview 脚本时也会自动执行这个短测试。

烟测检查公网 HTTPS 可达、未授权请求被拒绝、OAuth discovery、DCR、PKCE、资源绑定、
JSON MCP initialize/tools/list、真实桌面截图、浏览器快照、终端调用、带鉴权的 VNC WebSocket，
以及注销后 token 失效。interactive preview 通过烟测后会重启清掉测试客户端并恢复 READY。

参考：
- [Cloudflare Quick Tunnel 限制](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [Cloudflare tunnel 参数](https://developers.cloudflare.com/tunnel/reference/run-parameters/)
- [GitHub Actions 时限](https://docs.github.com/en/actions/reference/limits)
- [OpenAI MCP 鉴权](https://developers.openai.com/plugins/build/auth)
