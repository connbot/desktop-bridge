# Agent Computer 初始化向导

可本地运行的中文向导和独立服务端。GitHub 必需；Cloudflare 账号与域名可跳过。
使用自己的固定域名时，需要用户在 Cloudflare 已激活的域名；本项目不提供平台域名。

**这是未部署、尚未通过真实账号/浏览器验收的实现。**
默认缺少服务端 App 凭据时会阻止真实初始化，不会切换成假成功。
显式 mock 模式只用虚构资源，页面常驻模拟标识，地址不能连接。

从仓库根目录：

```sh
python -m pip install -c constraints.txt -r requirements-initializer.txt
INITIALIZER_MODE=mock INITIALIZER_ORIGIN=http://127.0.0.1:8765 \
  INITIALIZER_DATABASE=/tmp/initializer-mock.sqlite3 \
  python -m uvicorn initializer.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

打开 `http://127.0.0.1:8765`。

完整运行、状态机、安全边界、清理与上线清单见
[docs/initializer.md](../docs/initializer.md)。
