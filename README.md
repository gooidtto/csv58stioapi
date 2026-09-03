# Railway Universal Stable Deployment V5.5

## 部署

1. 导入 GitHub。
2. Railway 创建项目并连接仓库。
3. 使用 `Dockerfile` 部署。
4. 在 Service → Variables 添加 Railway Token。
5. 程序自动检查/创建 Public Domain 与 TCP Proxy（Target 8080）。
6. 自动请求 Redeploy，并在新 Deployment 中生成节点。
7. 部署成功后 Logs 直接打印 `SUBSCRIPTION_URL`、Node 1–5 状态和 `/ready` 状态。

## 节点

- Node 1：VLESS XHTTP TLS
- Node 2：VLESS RAW REALITY Vision
- Node 3：VLESS XHTTP REALITY
- Node 4：VLESS gRPC REALITY
- Node 5（可选）：Cloudflare Tunnel + VLESS XHTTP TLS

`/health` 保持 liveness；`/ready` 用于完整应用就绪判断。
