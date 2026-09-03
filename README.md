# Railway Universal Stable Deployment V5.5

## 部署方式（一次人工配置，之后自动运行）

1. 导入 GitHub 仓库并让 Railway Service 连接该仓库。
2. Service 使用仓库中的 `Dockerfile` 部署。
3. 在 **Service → Volumes** 创建 Persistent Volume，并将 **Mount Path 固定为 `/data`**。
4. 在 **Service → Variables** 只需人工设置一次：`RAILWAY_TOKEN=项目 RAILWAY_TOKEN`。
5. 点击 **Deploy**。
6. 程序启动时首先验证 `/data` 是真实挂载的 Persistent Volume；如果为空，则只初始化一次 UUID、REALITY key、3 个 Short IDs 和 subscription token，并写入 `/data`。
7. 程序自动检查/创建当前 Deployment 所需的 Public Domain 与 TCP Proxy（Target 8080）；如创建了网络资源，会自动请求 Redeploy。
8. 后续 Deployment / Restart / Container recreation 都直接从 `/data` 读取并复用同一套身份；运行时 endpoint、runtime manifest 和订阅内容则根据当前 Deployment 的 Railway 网络环境重新生成。

**Volume 必须在首次正式 Deploy 前完成挂载。** 程序不会把临时容器文件系统中的身份当作永久身份，也不会在缺少 Volume 时生成一套以后无法可靠保留的身份。

## 节点

- Node 1：VLESS XHTTP TLS
- Node 2：VLESS RAW REALITY Vision
- Node 3：VLESS XHTTP REALITY
- Node 4：VLESS gRPC REALITY
- Node 5（可选）：Cloudflare Tunnel + VLESS XHTTP TLS

## 身份持久化策略

节点身份唯一持久来源为 `/data`：

- `uuid.txt`
- `reality_private_key.txt`
- `reality_public_key.txt`
- `reality_short_ids.json`
- `subscription_token.txt`
- `.node-identity-initialized`

身份策略是 **`INITIALIZE_ONCE_REUSE_FOREVER`**：

- 空 Persistent Volume：初始化一次。
- 已初始化且完整有效：`NODE_IDENTITY=REUSED`。
- 已初始化但身份文件缺失、损坏或不完整：**拒绝启动，不生成新身份**。
- 未挂载 Persistent Volume：**拒绝启动，不生成临时身份**。
- `generate.py` 只读取 Short IDs，不负责生成身份。

## 运行时网络

Railway 的 Public Domain、TCP Proxy domain/port 属于当前 Deployment 的运行时环境，不属于节点身份。每次启动以当前 Railway 环境为 endpoint 权威来源，因此网络 endpoint 可以变化，而 UUID / REALITY key / Short IDs / subscription token 不变。

`RAILWAY_TOKEN` 只用于 Railway API 的网络资源 bootstrap。程序不会通过 Railway API 修改节点身份、国家/地区或用户未授权的外部配置。

## 长期运行

主进程由 `boot.sh` 直接运行。Gateway 先绑定 8080，随后完成 runtime、订阅、Xray listener 和可选 Cloudflare Tunnel 的启动检查。Gateway 或 Xray 主进程异常退出时，容器以失败状态结束，由 Railway `ON_FAILURE` 负责重新启动；重新启动后仍从 `/data` 复用同一身份。

Railway healthcheck 使用 `/health` 作为早期 liveness 检查；`boot.sh` 自己在完成运行时和 Xray readiness 检查后保持主进程运行。

## 验收重点

首次部署应出现：

```text
PERSISTENT_VOLUME=/data
PERSISTENT_VOLUME_MOUNT=PASS
NODE_IDENTITY=INITIALIZED
NODE_IDENTITY_FINGERPRINT=<fingerprint>
```

后续重启/重新部署应出现：

```text
PERSISTENT_VOLUME=/data
PERSISTENT_VOLUME_MOUNT=PASS
NODE_IDENTITY=REUSED
NODE_IDENTITY_FINGERPRINT=<same fingerprint>
```

同一 Volume 的 fingerprint 必须保持不变。 
