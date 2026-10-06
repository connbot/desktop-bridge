# Agent Computer 中文上手

如果不想自备服务器，可参考可选的 [E2B 上手说明](e2b-deployment.zh-CN.md)。需要先有经过构建和验证的模板，下面的本地 Docker 操作仍然适用。

[English](quickstart.md) · [简体中文](quickstart.zh-CN.md) · [项目介绍](../README.md)

**想让 ChatGPT 也像 Muse、Dots 那样操作电脑？** Agent Computer 把你自托管的
Linux 桌面、Chromium、终端和文件接入 ChatGPT。你可以看着它操作、随时接管，
再把完成的文件下载下来。文件与代码工具由 [Coding Tools MCP](https://github.com/xyTom/coding-tools-mcp) 提供。

模型、规划和任务推进由客户端负责。本项目提供电脑，没有内置模型循环或调度器；
客户端停止调用后，服务不会自行持续思考。这是单一可信用户的自托管预览版。

## 开始前确认

- **运行环境：**已验证 Linux x86-64。为容器预留 3 GB 内存，宿主机另留余量，
  磁盘需要容纳浏览器镜像和你的文件。macOS/Windows Docker Desktop 与 ARM 尚未认证。
- **本机工具：**Git、Python 3.11+、[Docker 与 Compose 插件](https://docs.docker.com/compose/install/)。
  先启动 Docker。下面命令使用 Bash 等 POSIX shell；Windows 需要等效终端和 Linux 容器环境。
- **ChatGPT 账号：**电脑操作需要自定义 MCP 的写入权限。按 2026 年 10 月 5 日核对的
  [OpenAI 官方要求](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)，
  完整写入支持 Business、Enterprise、Edu 的网页端，并受工作区权限限制；Pro 的自定义 MCP
  目前仅支持读取/获取。先确认账号权限，再为部署付费。
- **远程接入：**本教程使用你控制的 HTTPS 域名和服务器。本地观看页能打开，不代表 ChatGPT
  能访问你电脑上的 localhost。其他 MCP 客户端需要支持 Streamable HTTP、OAuth 授权码、
  S256 PKCE 和动态客户端注册；客户端兼容性仍需逐个验证。

这条直接连接 ChatGPT 的路径不需要 OpenAI 模型 API key，但你仍需合适的客户端账号，
自行承担托管费用。可选记忆和外部 MCP 服务默认关闭，首次任务不需要配置它们。

## 1. 启动电脑

在运行 Docker 的机器上执行：

```sh
git clone https://github.com/connbot/desktop-bridge.git
cd desktop-bridge
python3 scripts/setup.py
python3 scripts/doctor.py
docker compose up --build -d
```

产品名是 Agent Computer；仓库目录、Python 包和命令名仍保留 `desktop-bridge`，
环境变量仍使用 `BRIDGE_*`。首次构建会下载浏览器、系统包和中文字体。

```sh
docker compose ps
python3 scripts/doctor.py --running
```

`doctor.py` 只检查环境，不安装软件、不修改配置、不连接 ChatGPT。`--running` 额外检查
本机 8080 的就绪状态。刚构建完尚未就绪时，稍等再运行。失败时在本机查看
`docker compose logs --tail=100 desktop`；分享日志前先去除私人信息。

**成功标志：**在 Docker 宿主机的浏览器打开 **http://localhost:8080**。
用自己的编辑器打开 `.env`，把 `BRIDGE_OWNER_TOKEN` 的值填入观看页登录框。
不要发到聊天、Issue、源码或日志，也不要把它当作 MCP Bearer token。
安装脚本首次创建私有 `.env`，再次运行不会覆盖已有配置。

登录后应看到 Linux 桌面和 Chromium。默认只能观看。点击 **Take control** 并等待在途操作
结束后，才能输入鼠标键盘。下一步前点 **Hand back to AI** 交还控制。

## 2. 分清本地地址和远程地址

| 地址 | 使用位置 |
| --- | --- |
| `http://localhost:8080` | Docker 宿主机的浏览器，尚未切换 HTTPS 时 |
| `https://你的域名/mcp` | 完成 HTTPS 部署后的远程 MCP 客户端 |
| `http://127.0.0.1:8080/static/demo.html` | Agent Computer 容器里的 Chromium，用于本地示例 |

`localhost` 指发出请求的那台机器。ChatGPT 不能直接访问你的本机回环地址。
按 [HTTPS 部署教程](deployment.md) 配置域名与反向代理，将 `.env` 中的
`BRIDGE_PUBLIC_URL` 改为实际 HTTPS origin，例如 `https://computer.example.com`，
不要加 `/mcp`。然后运行 `docker compose up -d` 使新配置生效。

只代理 8080 网关，并让 Docker 端口继续绑定 `127.0.0.1`。代理必须转发整个站点，
包括 OAuth 路由与 WebSocket。不要公开 VNC 的 5900/5901、CDP 的 9222，或挂载 Docker socket。
切换 HTTPS 后，观看页也应使用这个完全一致的 HTTPS 地址。

只想短暂试用，可用 [GitHub Actions 临时预览](actions-preview.zh-CN.md)。它是按需测试环境，
结束后文件和浏览器状态都会消失。OpenAI 另有 [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
私网方案，但本项目尚未验证该路线；它还有独立权限、凭证和 OAuth 可达性要求，不能直接替换本教程。

**成功标志：**公网 HTTPS 观看页证书正常，部署检查里的 OAuth 元数据使用正确域名。
这只说明网络与配置可用，接下来还要验证真实客户端调用。

## 3. 接入 ChatGPT

按 [OpenAI 当前接入说明](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
在有权限的账号/工作区开启 developer mode 并创建自定义 app。界面名称可能随版本变化。

- 名称：`Agent Computer`
- MCP 地址：`https://你的域名/mcp`
- 认证：**OAuth**
- 若询问客户端注册方式：**Dynamic client registration / DCR**，无需静态 client secret

扫描工具时会跳转到**你的 Agent Computer 域名**。登录后核对客户端和回调地址，再批准访问。
回到 ChatGPT 完成创建，在普通聊天中选择这个 app，并把桌面观看页放在旁边。
这条路径使用普通聊天里的自定义 app；ChatGPT 独立的 agent mode 不调用自定义 app。
后续要再次操作时，可能需要重新选择或提及 app。客户端要求确认时，先检查再批准。

**成功标志：**发送“请使用 Agent Computer 查看会话状态并截一张图，不要登录任何网站”。
应看到实际工具调用，截图与观看页是同一个桌面。仅扫描到工具还不算完成验证。

访问 token 有效期一小时，本版没有 refresh token，过期后重新授权。服务重启也会清空
注册与授权；若客户端仍保存旧 client ID 并报 unregistered client，需要重新创建连接。

## 4. 完成第一个可检查的任务

在选择了 Agent Computer 的聊天中发送：

> 请使用 Agent Computer。先检查会话状态；若为 READY 就开始 AI 控制，若我暂停或接管了，
> 请让我手动交还。在电脑的浏览器里打开 http://127.0.0.1:8080/static/demo.html，
> 在 Project note 填入“你好，Agent Computer”，点 Save note，再读取页面确认保存提示。
> 在工作区创建 hello-agent-computer.txt，内容是同一句话，并读取文件验证。
> 最后告诉我文件名。只使用这个本地示例页面，不登录外部账号。

逐项检查：

1. 共享 Chromium 显示 acceptance lab 和 `Saved: 你好，Agent Computer`。
2. 客户端确实读回了刚创建的文件。
3. 在观看页 **Your files** 点 **Refresh**，下载 `hello-agent-computer.txt`，打开检查内容。

页面里的 **Save note** 只更新页面显示；后面的单独创建文件才产生可下载结果。
这一步能检查浏览器、文件工具和下载，不依赖外部网站。只有截图或一句“完成了”还不够。

### 下一步：做一个小工具，再修改它

这是需要启动本地服务器的可选练习。默认 `safe` 命令策略可能以 `PERMISSION_REQUIRED`
拒绝该命令。遇到这种情况，保留已生成的文件，停止被拒绝的步骤；不要让模型自动修改权限模式
或换写法绕过拒绝。所有者可另外审阅 [命令权限说明](deployment.md#command-permissions)。
上面的首次任务不需要 shell 服务器。

> 在工作区的 budget/index.html 创建单文件 HTML 预算计算器，包含交通、餐饮、门票三个
> 数字输入框，初值为 20、40、30，自动显示合计。在工作区用受管理命令启动
> `python3 -m http.server 8765 --bind 127.0.0.1 --directory budget`，
> 用电脑里的 Chromium 打开 http://127.0.0.1:8765，确认合计是 90；把门票改成 50，
> 确认合计是 110。读回保存的文件，告诉我下载路径。只用示例数据，不安装软件包。

再追加：“增加杂费一栏，初值 10。交通、餐饮、门票设为 20、40、50，检查合计是否为 120。”
结构化浏览器只接受 HTTP(S)，因此需要本地静态服务器，不能直接用 `file://`。
让服务器保持为受管理进程，不要后台脱离管理，也不用额外开放公网端口。接管可能停止这个进程，
交还控制后可让客户端重新启动。这些是任务示例，模型的成功率没有保证；实际测试见 [验证记录](validation.md)。

## 常见问题

| 现象 | 下一步 |
| --- | --- |
| 找不到 Docker / daemon 不可用 | 安装并启动 Docker，重新运行 doctor。不要用放宽 socket 权限来掩盖问题。 |
| 构建或启动失败 | 检查内存、磁盘和本地容器日志；构建需要能访问依赖源。 |
| 观看页有了，但画面为空 | 点 **Reconnect screen**，检查就绪状态与代理的 WebSocket 转发。 |
| OAuth 跳到 localhost / 错误域名 | 修正 `BRIDGE_PUBLIC_URL`，只写 HTTPS origin，运行 `docker compose up -d`。 |
| 改成 HTTPS 后本地登录/画面失败 | 改用配置中的 HTTPS 观看地址；cookie 和 WebSocket 都检查该 origin。 |
| ChatGPT 没有创建入口/写入工具 | 核对账号和工作区权限；服务可访问不代表账号有权限。 |
| `PERMISSION_REQUIRED` | 默认 `safe` 策略可能拒绝该命令。停止这一步，保留现有结果，让所有者审阅权限。不要自动切换 `trusted`/`dangerous`，也不要改写命令绕过拒绝。 |
| `UNAUTHORIZED` / `INVALID_GRANT` | 重新授权；授权码短时有效且只能用一次，访问 token 一小时过期。 |
| 重启后 `unregistered client` | 重新创建连接，重新注册客户端。 |
| `CONTROL_NOT_OWNED` | 在观看页点 **Hand back to AI**。模型不能自行解除接管、暂停或停止；AI 空闲约 30 分钟也会暂停。 |
| `STALE_OBSERVATION` | 重新截图/获取浏览器快照，再做下一步。 |
| `OUTCOME_UNKNOWN` | 先检查页面、文件或外部状态；不能换个 action ID 就盲目重放可能已完成的操作。 |
| `AMBIGUOUS_TARGET` | 刷新快照，使用唯一且完全匹配的 accessible role/name，或检查截图。 |
| 文件找不到 / 下载 413 | 确认文件在 `/data/workspace`。观看页单文件下载上限 20 MiB；更大文件用宿主机 Docker 工具取出。 |
| 偏好存储显示 disabled | 默认如此，不影响首次任务。确有需要时再配置 [context provider](context-providers.md)。 |

需要帮助时按 [问题反馈清单](../CONTRIBUTING.md#getting-help) 提供脱敏信息，不要公开 token 或私人文件。

## 关闭、恢复与安全边界

```sh
docker compose stop
docker compose start
```

命名卷保留工作区文件、浏览器 profile 和动作收据。运行中的进程和授权不会恢复，
模型任务也不会自行继续。下载重要结果，私下备份数据。不要把 `docker compose down -v`
当作排错命令，它会删除卷内数据。

这是单一可信用户的环境，不是多租户强隔离。浏览器、网关与任意 shell 共用容器，
Chromium 的内部 sandbox 未启用。Private takeover 阻止模型观察与工具访问，但不能撤销
已提交的外部动作，也不能保证终止脱离管理的后台进程。请先使用公开信息和测试数据，
连接私人账号前阅读 [安全说明](../SECURITY.md)。
