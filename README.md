# Mira CLI

通过命令行访问 Mira 账号、画布、素材和创作任务。当前版本：**v0.1.0**。

[下载最新 CLI 压缩包](https://github.com/taiyangyu111/mira/releases/latest/download/mira-cli.zip) · [版本与校验文件](https://github.com/taiyangyu111/mira/releases/latest)

此下载包仅包含 CLI，不包含 MCP 服务端。需要 Python 3.11 或更高版本，以及可访问的 Mira 服务和访问令牌。

## 安装

下载并解压 `mira-cli.zip`，进入解压后的 `mira-cli` 目录。

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mira.py --help
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python mira.py --help
```

以下示例使用 Windows PowerShell；macOS / Linux 将 Python 路径替换为 `.venv/bin/python`。

## 连接 Mira

先在 Mira 网站的 `/plugins` 页面获取访问令牌。设置你实际使用的 Mira 站点地址；以下域名只是占位示例：

```powershell
$env:MIRA_URL = 'https://your-mira-domain.example'
.\.venv\Scripts\python.exe mira.py auth-save
.\.venv\Scripts\python.exe mira.py account
.\.venv\Scripts\python.exe mira.py canvas-list
```

`auth-save` 会隐藏输入，并在校验成功后将令牌保存到系统凭据库。远程地址必须使用 HTTPS，且不包含路径或查询参数。未设置 `MIRA_URL` 时连接本机 `http://127.0.0.1:8091`。

macOS / Linux 使用 `export MIRA_URL='https://your-mira-domain.example'` 设置地址。无系统凭据库的环境可通过当前进程环境变量 `MIRA_TOKEN` 提供令牌；该变量优先于保存的令牌。不要将令牌写入代码或提交到仓库。

清除本机保存的当前站点令牌：

```powershell
.\.venv\Scripts\python.exe mira.py auth-clear
```

`auth-clear` 不会清除环境变量，也不会撤销网站上的令牌。PowerShell 可用 `Remove-Item Env:MIRA_TOKEN -ErrorAction SilentlyContinue` 清除当前终端的令牌变量。

## 参数与常用操作

操作参数支持 `--json` 或 `--file`，两者不能同时使用。复杂参数建议保存成 UTF-8 JSON 文件，避免终端引号转义问题。

例如创建 `canvas-get.json`，把示例 ID 换成 `canvas-list` 返回的画布 ID：

```json
{"project_id": "替换为画布ID"}
```

```powershell
.\.venv\Scripts\python.exe mira.py canvas-get --file canvas-get.json
.\.venv\Scripts\python.exe mira.py model-list
.\.venv\Scripts\python.exe mira.py task-get --help
```

| 类别 | 命令 |
| --- | --- |
| 账号 | `account`、`auth-save`、`auth-clear` |
| 画布 | `canvas-list`、`canvas-get`、`canvas-create`、`canvas-apply` |
| 资产 | `asset-search`、`asset-import`、`asset-upload` |
| 模型与估价 | `model-list`、`generation-quote` |
| 创作 | `text-generate`、`image-generate`、`video-generate`、`audio-generate` |
| 任务 | `task-get`、`task-download` |
| 素材 | `material-list`、`material-get`、`material-preparations`、`material-refresh-previews`、`material-authenticate`、`material-retry`、`material-reference` |

命令提供固定操作入口；具体业务参数和模型能力取决于所连接的 Mira 服务版本。`--help` 展示命令及通用参数，不包含完整业务参数结构。

编辑画布前先读取当前版本，`canvas-apply` 使用 `baseVersion` 和 `operationId`。同一操作重试沿用 `operationId`；发生版本冲突后重新读取画布。

生成可能消耗积分，四个 `*-generate` 命令必须显式传入 `--confirm-spend`。图片、视频可先通过 `generation-quote` 查询报价；估价失败不代表免费。异步任务使用返回的任务 ID，通过 `task-get` 查询进度。

`asset-upload` 的 `file` 参数为本地文件路径，仅支持不超过 80 MB 的图片、视频和音频。`task-download` 使用 `task_id` 和 `output` 参数下载已归档结果，不覆盖已有文件。

此版本已移除前端隐藏功能对应的独立命令，包括多角度图片、视频剪辑、抽帧、抽音频、语音合成、语音识别、剧本生成和 3D 生成。服务端权限及批量编辑校验由所连接服务负责。

## 下载校验

从同一 Release 下载 `SHA256SUMS.txt`，然后计算压缩包的 SHA-256 并与文件中的值比较：

```powershell
Get-FileHash .\mira-cli.zip -Algorithm SHA256
```

macOS 可用 `shasum -a 256 mira-cli.zip`，Linux 可用 `sha256sum mira-cli.zip`。

## 常见问题

- 连接失败：检查 `MIRA_URL`、服务是否启动及本机网络。当前 CLI 不读取系统代理环境变量。
- `401` / `403`：检查访问令牌、有效期及服务端权限。
- 凭据库不可用：使用操作系统支持的凭据库，或通过当前进程的 `MIRA_TOKEN` 配置令牌。
- 更新：下载最新压缩包，重新安装其中的依赖；保存在系统凭据库的令牌按站点地址复用。

当前提供 Python 脚本包，尚无免安装的独立可执行文件。
