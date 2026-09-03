# Railway Universal Stable Deployment V5.5

## 部署

1. 导入 GitHub。
2. Railway 创建项目并连接仓库。
3. 使用 `Dockerfile` 部署。
4. 在 Service → Variables 添加 Railway Token。
5. 如启用 Node 5，保持 Cloudflare 6 项变量完整且不要在运行期间手动修改。
6. 程序自动检查/创建 Public Domain 与 TCP Proxy（Target 8080）。
7. 自动请求 Redeploy，并在新 Deployment 中生成节点。
8. 部署成功后 Logs 直接打印 `SUBSCRIPTION_URL`、Node 1–5 状态和 `/ready` 状态。

## 节点

- Node 1：VLESS XHTTP TLS
- Node 2：VLESS RAW REALITY Vision
- Node 3：VLESS XHTTP REALITY
- Node 4：VLESS gRPC REALITY
- Node 5（可选）：Cloudflare Tunnel + VLESS XHTTP TLS

## 长期运行 / 自动自愈

在 `RAILWAY_TOKEN`、Node 5 Cloudflare 变量以及国家/地区等外部配置没有人为变更时，运行时自愈不会主动重新配置节点，也不会主动改变节点身份信息。

- `UUID`、REALITY key 和 short IDs 使用 `/data` 中的持久化文件保存；正常重启/自愈重启会复用它们。
- 每次启动仍以**当前 Deployment 的 Railway 网络环境**为 endpoint 权威来源，因此只有 Railway 网络本身发生变化时才会重新生成 endpoint 信息。
- Supervisor 只做健康检查，不修改 Railway Variables、不调用节点生成逻辑来“换节点”。
- `/ready` 检查 runtime、订阅、全部 Xray listener，以及启用 Node 5 时的 Cloudflare Tunnel。
- 连续健康检查失败后，Supervisor 会退出容器，由 Railway `ON_FAILURE` 自动重启；重启后复用持久化节点身份。
- Railway healthcheck 使用 `/ready`，避免“容器还活着但客户端节点已经不可用”时被错误判定为 Healthy。

**重要：`/data` 必须挂载 Railway Persistent Volume。** 如果没有持久化卷，容器重建后无法保留 UUID/REALITY key/short IDs，任何代码都无法在纯临时文件系统中保证节点身份永久不变。

`/health` 保持兼容；Railway 部署健康检查使用 `/ready`。
