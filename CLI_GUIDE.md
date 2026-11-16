# CLI 使用说明（v0.4.0）

下列命令中的 `python` 指安装了 requirements.txt 的虚拟环境解释器。原有 `--json`、`--file`、`--confirm-spend` 保持使用；默认仍输出完整接口 JSON。

## 查看真实字段与示例

```powershell
python mira.py image-generate --help
python mira.py image-generate --schema
python mira.py image-generate --example
```

23 个接口命令均提供必填/可选字段、类型、已知范围和示例。`--schema` 包含嵌套字段定义；`--example` 输出可保存为 UTF-8 JSON 的参数对象。这三个选项不连接服务端。示例中的资源 ID、模型 ID、版本号和本地文件路径需要按实际情况替换。

通用规则从当前服务端公开请求模型导出，覆盖字段结构和基本范围；它不是实时模型能力表，也不包含全部业务校验。`parameters` 等扩展对象以服务端规则为准。

## 提交前检查

保存 `image.json`：

```json
{"prompt":"一只橘猫坐在窗边，午后自然光","size":"1024x1024","numImages":1}
```

```powershell
python mira.py image-generate --file image.json --validate-only
python mira.py image-generate --file image.json --confirm-spend
```

`--validate-only` 不联网、不上传、不提交生成、不消耗生成积分，也不要求令牌或 `--confirm-spend`。返回 `valid`、`errors`、`warnings`、`notChecked`，错误会列明字段路径；失败退出码为 2。普通提交也会先做同样的检查。

检查包括必填字段、类型、枚举、数值范围、图片尺寸格式、常见跨字段错误，以及上传文件存在性/后缀/大小。上传检查不解码媒体，也不替代素材审核。模型实时可用性、模型专属限制、余额、权限、素材审核、画布版本和业务结构仍由服务端判断。检查通过不代表一定能生成成功。

本地规则使用 JSON 原生类型，例如数量写 `1`，不要写 `"1"`。未知字段会报错，避免拼写错误被服务端忽略。确认较新的服务端支持当前客户端尚未收录的参数时，可以显式使用 `--skip-validation`；这只跳过 CLI 检查，不绕过服务端校验，也不能免除生成确认。

## 查看指定类型和模型

```powershell
python mira.py model-list --kind image
python mira.py model-list --kind video
python mira.py model-list --summary
python mira.py model-info --model "替换为模型ID" --kind image
```

这些命令使用当前服务的 `miraModels` 公开目录。`model-info` 支持精确模型 ID、上游 ID 或唯一展示名，返回公开配置和对应生成命令的通用 `parameterSchema`。

图片模型的 `resolutions`（如有）来自服务端配置。当前目录未提供的参考素材数量、输出数量、格式等限制，在 `limits` 中显示 `null`，意思是**未知**，不是无限制。通用接口的数量上限也不是每个模型实际支持的数量。目录未提供音频模型时会明确提示，不据此判断音频生成不可用。

## 画布摘要与单节点详情

先准备 `canvas.json`：

```json
{"project_id":"替换为真实画布UUID"}
```

```powershell
python mira.py canvas-get --file canvas.json --summary
python mira.py canvas-get --file canvas.json --node-id "替换为节点ID"
python mira.py canvas-get --file canvas.json --node-id "替换为节点ID" --summary
```

摘要包含画布 ID、标题、版本、节点 ID/名称/类型/状态及连接。指定节点后只输出该节点及其相关连接；不加 `--summary` 时保留节点完整内容。节点不存在会明确报错。

筛选在 CLI 内完成：减少的是终端输出和 AI 上下文，当前不会减少服务端返回给 CLI 的网络数据量。

## 任务摘要与字段筛选

准备 `task.json`，内容为 `{"task_id":"替换为真实任务UUID"}`：

```powershell
python mira.py task-get --file task.json --summary
python mira.py task-get --file task.json --fields "task_id,task.status,task.result_url,task.error_message"
python mira.py task-get --file task.json --summary --fields "taskId,status,error"
```

任务摘要只包含任务 ID、状态、进度、结果地址和错误信息。结果地址只读取服务端公开字段，不从内部元数据中找回被审核隐藏的地址。

`--fields` 适用于接口命令，支持点分隔路径与数组下标，如 `items.0.id`。与摘要组合时，字段路径针对摘要结果。不存在的字段返回 `null` 并在 `_missingFields` 中列明；不会因为筛选失败而自动重新提交请求。

## 完整响应保存到文件

```powershell
python mira.py canvas-get --file canvas.json --save-response full-canvas.json
python mira.py canvas-get --file canvas.json --summary --save-response full-canvas-2.json
```

`--save-response` 保存筛选前的完整接口 JSON。单独使用时，终端只输出保存路径及可用的资源/任务 ID；与摘要、节点、类型或字段筛选同时使用时，文件保存完整响应，终端显示筛选结果。

目标目录必须存在，目标文件不能已存在。CLI 会在请求前检查并占用输出文件，避免生成后才发现文件冲突。如果请求已经返回而写盘失败，会输出原始响应并提示核对任务，不能盲目重提生成。

## 更新

更新时替换整个包并重新安装 requirements.txt；不要只复制 mira.py。CLI 现在还需要 mira_cli_support.py、mira_cli_jobs.py、mira_contracts.json 和 jsonschema 依赖。

## 自动等待与回填（v0.3.0）

适用于 `image-generate`、`video-generate`、`audio-generate`。文本生成当前仍为同步调用，不使用这组任务等待选项。原有不带等待选项的提交方式保持原样。

生成时等待，并把结果放回已经存在的媒体节点：

```powershell
python mira.py image-generate --file image.json --confirm-spend --wait --project-id "画布UUID" --node-id "图片节点ID" --timeout 600 --run-file cat-run.json
```

参数文件仍只放生成参数。`--project-id`、`--node-id` 是 CLI 选项，必须成对出现。CLI 会在提交前检查节点存在、节点类型与生成类型一致。请先通过 `generation-quote` 查看报价；等待功能不会替你确认消费。

只等待结果，不回填：

```powershell
python mira.py video-generate --file video.json --confirm-spend --wait --timeout 600 --run-file video-run.json
```

指定回填目标或 `--run-file` 也会启用等待。默认每 5 秒查询一次，可用 `--poll-interval` 调整为 1–60 秒；默认等待预算为 600 秒，`--timeout 0` 只检查当前状态。预算针对提交后的轮询，提交和回填还有独立的网络请求时间，不是整个命令的总耗时上限。

任务提交后会立即在 stderr 输出任务 ID 和记录路径；stdout 最终输出一份 JSON 回执。记录文件默认保存在当前目录 `.mira/runs/<随机ID>.json`，也可以显式指定 `--run-file`。记录包含站点、任务 ID、画布和节点 ID、回填操作 ID、结果地址及执行状态，不保存访问令牌和生成参数。不要把私人任务记录提交到公共仓库。

超时、中断或回填失败后，使用原记录接续：

```powershell
python mira.py task-resume --run-file cat-run.json --timeout 600
```

`task-resume` **不会重新生成**：先查询原任务的服务端状态；已成功的任务只进行回填或确认已回填。即便进程在回填成功后丢失响应，也会识别节点上已有的相同结果。同一记录只能由一个 CLI 进程使用。

已有任务但没有记录文件时：

```powershell
python mira.py task-wait --task-id "任务UUID" --timeout 600
python mira.py task-wait --task-id "任务UUID" --project-id "画布UUID" --node-id "媒体节点ID" --timeout 0 --run-file attach-existing.json
```

上述命令不要求 `--confirm-spend`，因为不会提交生成；指定目标仍会编辑画布，需要对应的读写权限。

### 状态与退出码

| phase | 含义 | 退出码 |
| --- | --- | --- |
| `completed` | 已取得可公开的媒体结果，没有请求回填 | 0 |
| `attached` | 已成功回填，或确认目标已有相同任务结果 | 0 |
| `wait_timeout` / `interrupted` | 等待结束，服务端任务未取消；可继续接续 | 3 |
| `generation_failed` | 生成失败或被取消，不尝试回填 | 4 |
| `attach_failed` | 生成成功，但回填失败；接续只重试回填 | 4 |
| `submission_unknown` | 提交响应丢失或没有任务 ID；不能判断是否已创建任务 | 4 |
| `query_failed` / `preflight_failed` | 查询或提交前目标检查失败 | 4 |

回执还包括独立的 `generation`、`attachment` 状态、`taskId`、`projectId`、`nodeId`、`runFile`、错误原因及接续命令参数。

若提交网络超时且未收到任务 ID，CLI 不会自动重试提交。先在网站任务记录核对，找到任务 ID 后使用 `task-wait`，不要直接重新运行生成命令。已有记录文件不能用于再次提交生成。

### 回填保护与限制

- 只使用服务端公开的主结果地址；任务显示完成但没有公开地址时，继续等待或返回超时，不从内部元数据提取被隐藏的地址。
- 当前回填一个主结果到一个节点。多图任务的其他结果仍在服务端任务记录中，可通过 `task-get` 查看，不会自动创建多个节点。
- 回填前重新读取画布版本，仅更新目标节点的输出元数据，保留提示词等配置；服务端重新登记素材。版本冲突最多重读重试 3 次，同一回填始终沿用操作 ID。
- 如果等待期间目标节点发生变化，停止回填；确认之后，可以用 `task-wait` 显式指定目标，重新挂接同一个已有任务。这不会重新生成。
- 任务记录绑定原 `MIRA_URL`；接续时必须使用原站点和有权限的账号。
- CLI 关闭后不会在后台自动回填；服务端生成可以继续，重新运行 `task-resume` 才会继续等待和回填。
- `--save-response` 与等待模式组合时，保存的是最终流程回执，实时接续信息由 `--run-file` 保存。

## 画布同步显示（v0.4.0）

沿用 `--wait --project-id ... --node-id ...` 即可。任务拿到 ID 后，CLI 会同步排队、生成进度和失败原因；只有状态或进度变化时才写入画布。生成完成后同步结果。仅使用 `--wait` 而未指定节点时，不会写入画布。

配套新版画布在页面可见、没有正在输入或拖拽、没有未保存内容时，约每 2 秒检查外部更新。CLI 新建节点、更新状态和回填结果会自动显示，不必反复手动刷新。切回页面会主动检查一次；正在输入、拖拽或保存时会延后接收，不覆盖当前编辑。请求过程中产生了本地改动或切换了画布，也会丢弃旧响应。

自动显示需要**站点前端配套更新**，不是只安装 CLI 就能改变远端网页。当前已更新并验收本机画布；GitHub CLI 下载包不包含站点前端。此前已打开的本机页面需要刷新一次，载入新前端代码。

节点被关联到另一个任务或提示词等内容被修改时，CLI 停止覆盖该节点，但会继续查询原任务。状态写入失败会在 `statusSyncError` 中说明，生成结果仍可通过任务 ID 查询。v0.3.0 的任务记录可继续使用；不会因升级再次提交生成。

CLI 进程关闭后不会继续写进度；网页如果正在查询已关联任务，可能独立更新结果。可靠的 CLI 接续仍使用 `task-resume`。
