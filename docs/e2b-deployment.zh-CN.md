# E2B：不用自备服务器的 Agent Computer

这个可选方案把 Agent Computer 放进一个 E2B 沙箱，启动后直接打印桌面、OAuth 登录页和 `/mcp` 的 HTTPS 地址。文件、终端和代码编辑继续由 **[xyTom 的 Coding Tools MCP](https://github.com/xyTom/coding-tools-mcp)** 提供；模型与任务规划仍在 ChatGPT 中，不需要额外的模型 API Key。

**目前是集成预览，不是已经上线的公共模板。** 仓库包含镜像构建目标、模板构建器、启动器和真实环境验收脚本，暂时没有可直接推荐的、已经完成线上验收的模板 ID。维护者需要先构建并验证模板。单元测试通过不代表 E2B 部署或你的 ChatGPT 账号已经通过实测。

[English](e2b-deployment.md)

## 费用与准备

- 获得经过批准并完成构建的模板 ID 后，在 Linux/macOS 上使用 Python 3.11+；Windows 请使用 WSL，暂不支持原生 Windows Python。本次本地验证使用 Linux。不需要本地 Docker、服务器或域名。
- 准备自己的 E2B 账号、API Key 和模板 ID。Key 只放在自己电脑的可信终端环境变量中，不要发到聊天、写进模板或镜像、放进桌面工作目录，或提交到 Git。
- E2B 当前提供一次性 $100 额度，注册时无需信用卡；这不是永久不限量免费。Hobby 订阅费为 $0，计算资源按用量收费，连续运行上限为一小时。启动前检查实际剩余额度与最新价格。本模板申请 2 CPU、4 GiB 内存。
- 先确认 [ChatGPT 账号支持自定义 MCP 写入工具](quickstart.zh-CN.md)。HTTPS 地址能打开，并不能证明账号有相应权限。

[最新计费说明](https://docs.e2b.dev/billing) · [暂停与恢复](https://docs.e2b.dev/sandbox/persistence)

## 启动并连接 ChatGPT

在项目目录中：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-e2b.txt
# 在自己的可信终端中安全设置 E2B_API_KEY，不要把 Key 发到聊天。
python scripts/e2b_sandbox.py create --template YOUR_APPROVED_TEMPLATE_ID
```

`create` 只创建一个沙箱，在沙箱内安装限定范围的防火墙规则，确认 IPv4、IPv6 都通过后才启动桌面，再检查服务就绪并输出地址。执行前请阅读下方的安全说明并确认允许这些沙箱内网络变更；它不会修改你自己电脑的防火墙。

每次创建都会生成新的桌面登录 token，保存在本地私密文件 `~/.local/state/agent-computer/e2b.json` 中，权限为 0600。只在自己的电脑上打开这个文件，把其中的 `owner_token` 复制到**该桌面的登录页面**。不要发给 ChatGPT，也不要当作 MCP bearer token。文件还保存恢复沙箱所需的 ID，不包含 E2B 账号 API Key。

在 ChatGPT 创建名为 **Agent Computer** 的自定义应用，填入输出的 `/mcp` 地址，选择 OAuth；如有提示则使用动态客户端注册。前往同一个桌面域名登录，检查回调并授权。无需静态 OAuth client secret。OAuth 发现、登录、MCP 和桌面 WebSocket 都使用同一个完整 HTTPS 域名。

启动成功后可以退出启动器，不需要让笔记本持续开着隧道或代理。桌面和 MCP 保留现有的应用认证。

## 暂停、恢复和删除

```sh
python scripts/e2b_sandbox.py status
python scripts/e2b_sandbox.py pause
python scripts/e2b_sandbox.py resume
```

- `status` 只读取状态，不会唤醒暂停的沙箱。
- `pause`（也可以写成 `stop`）保留内存与文件。`resume` 恢复同一个 ID，检查防火墙、域名和服务状态，不会偷偷创建新沙箱或更换登录 token。
- 默认在 3,600 秒的运行期限到达时保留内存并暂停。这是期限，不是“检测空闲”。用 `--timeout 600` 可以申请十分钟。连接已经运行的沙箱只会延长既有期限，不能依赖它缩短期限。
- 默认关闭 HTTP 请求自动唤醒，避免陌生人访问公开地址消耗额度；需要使用时手动运行 `resume`。
- 内存暂停保留 `/data/workspace` 文件、`/data/profile` 浏览器资料、操作记录和内存中的认证状态。现有网络连接会断开，恢复后刷新查看器、重新连接 MCP。OAuth token 可能在暂停期间过期，需要重新授权；浏览器资料保留也不保证第三方网站永远保持登录。
- 不要选择仅保存文件系统的暂停或 `on_resume="reboot"`：它们丢失运行中的防火墙与进程状态，本启动器不支持这种冷启动。
- 暂停快照不是备份。及时导出重要文件。`kill` 或删除沙箱会永久丢失沙箱文件；从同一模板新建沙箱不能恢复这些资料。

导出需要保留的资料后，才能永久删除：

```sh
python scripts/e2b_sandbox.py delete --confirm-id EXACT_SANDBOX_ID
```

可以把 `--state /PRIVATE/PATH/e2b-state.json` 放在子命令**前面**指定其他私密状态文件，之后每次操作都使用同一路径。恢复失败时不要直接再运行 `create`，每次新建都会产生新的资源。

启动失败时，只要已获得沙箱 ID 就会保存下来，并尝试暂停而非删除资料。保留本地状态文件，查看 E2B 状态。如果 API 超时导致文件只剩 `phase: allocating`，创建结果可能不确定，先查看 E2B 控制台再重试。反馈问题时不要上传状态文件、原始环境变量、密钥、cookies 或浏览器资料。

## 维护者：构建一次，供使用者直接启动

只有维护者或构建工作流需要 Docker。执行云端操作前，确认镜像仓库、E2B 账号、发布范围和费用已经获批。下面是操作说明，设置脚本不会自行运行这些命令。不要把个人资料或密钥烘焙进镜像。

```sh
# 明确使用 e2b target，不要使用 fly 或默认 local target。
docker build --target e2b -t YOUR_APPROVED_REGISTRY/agent-computer:e2b .
# 按获批的仓库发布流程上传镜像，获得其 digest 后：
python scripts/build_e2b_template.py \
  --image YOUR_APPROVED_REGISTRY/agent-computer@sha256:EXACT_IMAGE_DIGEST \
  --alias YOUR_TEMPLATE_NAME
```

模板使用固定版本 `e2b==2.52.1`，导入已经构建完成、按 digest 固定的镜像，避免把多阶段 Dockerfile 交给 E2B 翻译。资源为 2 CPU、4,096 MiB 内存。

E2B 的 start command 在**构建时**执行并被快照，不会在每次创建时重新执行。因此模板只运行 `sleep infinity`，ready command 只收紧 E2B 构建后调整过的 `/usr/local` 权限。模板不会启动 Chromium、网关、OAuth，也不生成用户 token 或浏览器资料。真正的桌面启动发生在创建沙箱之后，使用独立 token 和 `get_host(8080)` 返回的精确 HTTPS 域名。

[镜像导入](https://docs.e2b.dev/template/base-image) · [构建期启动语义](https://docs.e2b.dev/template/start-ready-command)

## 为什么必须额外保护入口

**E2B 可以把监听在 localhost 的端口代理到公网。** 原来的 VNC、CDP 只绑定 `127.0.0.1`，在这里并不能保证安全。为了兼容普通浏览器和 ChatGPT OAuth，本方案让 8080 可以不带 E2B 私有 header 访问，但仍由 Agent Computer 自身认证保护。

启动任何桌面进程前，受保护的 root 引导程序会分别建立 IPv4、IPv6 INPUT 规则：保留 loopback、已建立连接的返回流量，允许 TCP 8080 和 E2B 已认证的控制端口 49983，丢弃其他入站 TCP。不清空供应商原有规则，不改 OUTPUT，也不改变非 TCP 流量。任何工具缺失、规则不支持或验证失败都会阻止桌面启动。不会运行 Code Interpreter 的 49999 服务。

E2B 的构建后处理可能把 `/usr/local` 改为可写，并创建免密码 sudo 用户。模板的 ready command 会先移除 `/usr/local` 的组/其他人写入权限；root 引导使用 `/opt` 下受保护的代码和系统 `/usr/bin/python3 -I -S`，不导入可写的 Python site packages。每次 root SDK 命令都会在登录 shell 启动前明确设置受保护的 root HOME/PATH，并禁用环境指定的 shell 启动文件，避免读取桌面用户可写的 profile。之后以普通 `bridge` 用户启动，清空附加用户组与 capabilities，并让所有子进程继承 `no_new_privs`，阻止通过 sudo/setuid 重新获得 root、关闭防火墙。

E2B 账号 API Key 留在本地。沙箱内的模型终端、浏览器和应用仍属于同一位可信用户，不是彼此隔离的安全区。请先使用演示账号与低敏感资料。

[官方沙箱防火墙要求](https://docs.e2b.dev/network/restrict-public-access#running-a-firewall-inside-the-sandbox)

## 发布模板前必须验收

离线检查：

```sh
python -m pip install -c constraints.txt -e '.[test,e2b]'
ruff check .
pytest -q
node --test tests/viewer_state.test.cjs
```

E2B 镜像工作流只构建并检查冷镜像，不使用供应商 Key、不发布、不执行防火墙。启动器单元测试模拟供应商调用；SDK 构建器测试使用真实安装的 SDK，但不访问 E2B。

用户安全配置 Key，并明确批准一次真实沙箱、沙箱防火墙变更和费用后，创建**一次性测试沙箱**，再运行：

```sh
python -m pip install 'websockets==17.2'
python scripts/e2b_acceptance.py --run-live --timeout 600
```

脚本使用已保存的沙箱，检查辅助端口隔离、loopback 测试服务和防火墙证据、完整 OAuth/PKCE、真实 MCP 截图/浏览器/终端、查看器 WebSocket，以及内存暂停恢复后文件和真实浏览器状态是否保留。它会写入合成测试资料，完成后保持沙箱运行，需要自行暂停；不要对珍贵的个人会话运行。

也可以手动触发 `e2b-live.yml`，提供已经批准且与代码匹配的模板 ID，勾选一次性测试确认，并通过受保护的 GitHub `e2b-live-validation` 环境安全设置 `E2B_API_KEY`。工作流不发布镜像或模板，只创建一个测试沙箱、设置十分钟自动暂停、验收，然后即使失败也只删除本次测试沙箱。任务最长十五分钟。若执行器在清理前被强制终止，请到 E2B 检查；自动暂停不会删除留存资料。push/PR 不会触发真实 E2B 云端操作。

真实供应商验收通过后，仍需用实际 ChatGPT 账号完成 OAuth 和一个小任务，才能说明该账号可用。现有 Docker/Fly 部署方式及默认行为保持不变。
