# Mira CLI

通过命令行访问 Mira 账号、画布、素材和创作任务。当前版本：**v0.8.0**。

[下载最新 CLI 压缩包](https://github.com/taiyangyu111/mira/releases/latest/download/mira-cli.zip) · [版本与校验文件](https://github.com/taiyangyu111/mira/releases/latest)

此下载包仅包含 CLI，不包含 MCP 服务端。需要 Python 3.11 或更高版本，以及可访问的 Mira 服务和访问令牌。

## 分镜驱动的批量执行计划

新增 `production-create`、`production-preflight`、`production-select`、`production-run`、`production-resume`、`production-status`。从已应用的分镜布局整理每镜参数，整批校验、逐镜报价，费用授权后分阶段提交；图片审阅选择后才准备视频，已有任务不重新生成。

`production-run` 可能扣费，需要已审阅的报价ID、报价合计上限和 `--confirm-spend`；其余计划命令不提交新生成。报价过期、参数/价格变化或未知提交结果时停止。完整流程见 [执行计划指南](EXECUTION_GUIDE.md)。

## 模型规则、批量接续与成品验收

- `generation-check --kind video --file video.json`：读取当前站点模型规则，报告具体参数问题，不生成。实际提交可加 `--check-model`。旧站点没有规则时明确提示未检查；严格检查会停止提交。
- `batch-create`、`batch-status`、`batch-resume`：把已有任务记录组批，汇总状态、接续等待或仅重试回填，不重复生成；支持 `--summary`。
- `media-inspect --file final.mp4`：检查本机成品实际尺寸、时长、帧率、音轨，可与预期比较。需要 FFmpeg 中的 ffprobe。

见 [生产流程指南](PRODUCTION_GUIDE.md) 的完整参数、示例和能力边界。新模型规则需要相应服务端版本；仅升级 CLI 不会更新服务端。

## 批量分镜布局

新增 `storyboard-plan` 和 `storyboard-apply`，输入镜头列表即可预览并创建节点、连线和排版。支持说明→图片→视频、图片→视频、说明→图片，最多10镜。不会提交生成，重复应用同一计划不重复创建，版本冲突不覆盖现有画布。详见 [分镜布局指南](STORYBOARD_GUIDE.md)。

```powershell
python mira.py storyboard-plan --example
python mira.py storyboard-plan --project-id <画布UUID> --file shots.json --out plan.json
python mira.py storyboard-apply --file plan.json
```

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
| 模型与估价 | `model-list`、`model-info`、`generation-quote` |
| 创作 | `text-generate`、`image-generate`、`video-generate`、`audio-generate` |
| 任务 | `task-get`、`task-download`、`task-wait`、`task-resume` |
| 素材 | `material-list`、`material-get`、`material-preparations`、`material-refresh-previews`、`material-authenticate`、`material-retry`、`material-reference` |

`--help` 展示必填/可选字段及示例，`--schema` 查看完整嵌套参数，`--example` 输出示例 JSON。使用 `--validate-only` 可以先在本地检查参数，不联网、不提交生成。普通提交也会检查；实时模型能力和业务规则以服务端为准。

新增画布/任务摘要、单节点详情、模型类型筛选、字段筛选和完整响应保存。详细用法见 [CLI 参数与精简输出指南](CLI_GUIDE.md)。默认保持完整 JSON 输出。

```powershell
.\.venv\Scripts\python.exe mira.py image-generate --help
.\.venv\Scripts\python.exe mira.py image-generate --file image.json --validate-only
.\.venv\Scripts\python.exe mira.py model-list --kind image
.\.venv\Scripts\python.exe mira.py canvas-get --file canvas-get.json --summary
.\.venv\Scripts\python.exe mira.py canvas-get --file canvas-get.json --save-response full-canvas.json
```

`image.json` 的示例见指南。模型目录未提供的限制明确标记未知，不作推测。精简输出在 CLI 内完成，减少终端与 AI 上下文内容，不减少接口网络响应。

编辑画布前先读取当前版本，`canvas-apply` 使用 `baseVersion` 和 `operationId`。同一操作重试沿用 `operationId`；发生版本冲突后重新读取画布。

生成可能消耗积分，四个 `*-generate` 命令必须显式传入 `--confirm-spend`。图片、视频可先通过 `generation-quote` 查询报价；估价失败不代表免费。异步任务使用返回的任务 ID，通过 `task-get` 查询进度。

`asset-upload` 的 `file` 参数为本地文件路径，仅支持不超过 80 MB 的图片、视频和音频。`task-download` 使用 `task_id` 和 `output` 参数下载已归档结果，不覆盖已有文件。

此版本已移除前端隐藏功能对应的独立命令，包括多角度图片、视频剪辑、抽帧、抽音频、语音合成、语音识别、剧本生成和 3D 生成。服务端权限及批量编辑校验由所连接服务负责。

## 等待生成并回填节点

图片、视频、音频生成支持 `--wait`。指定目标画布和媒体节点后，CLI 会查询任务状态，成功后自动回填；超时或中断后使用任务记录继续，不会重新生成：

```powershell
.\.venv\Scripts\python.exe mira.py image-generate --file image.json --confirm-spend --wait --project-id "画布UUID" --node-id "节点ID" --run-file cat-run.json
.\.venv\Scripts\python.exe mira.py task-resume --run-file cat-run.json
```

回填失败会单独返回 `attach_failed`，接续只查询原任务并重试回填。等待期间节点被修改时停止覆盖。终端关闭后不会在后台自动回填；需要重新运行接续命令。当前每个目标节点回填任务的主结果。详见 [等待与接续说明](CLI_GUIDE.md)。

## 画布同步显示

v0.4.0 会把目标节点的排队、进度和失败原因同步到画布；配套新版网页可自动显示外部更新。网页正在输入、拖拽或保存时会延后同步。当前已在本机前端验收；远端站点需要单独部署配套前端，CLI 包不包含网页资源。详见 [画布同步显示说明](CLI_GUIDE.md)。

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
- 更新：下载最新压缩包，替换整个包并重新安装其中的依赖（v0.2.0 新增 jsonschema）；不要只替换 mira.py。保存在系统凭据库的令牌按站点地址复用。

当前提供 Python 脚本包，尚无免安装的独立可执行文件。


## AI 节点目录与流程（v0.8.0）

先用 `node-list`、`workflow-list` 选择节点与流程，再用 `node-info --node video` 或 `workflow-info --workflow nature-video` 按需查询。目录仅覆盖前端可见基础节点；支持字段筛选、创建节点示例与保存结果，不联网、不要求令牌、不提交生成。详见 [节点与流程指南](NODE_GUIDE.md)。

此版本也兼容视频服务的 queuing 排队状态，并让 --timeout 0 的单次状态检查保留正常网络响应等待时间。
