# CLI 使用说明（v0.9.9）

## v0.9.9 更新

- 任务查询、摘要和接续显示新版服务端返回的实际结算积分或待结算状态。
- Seed Audio 1.0 在时长未知时显示每秒积分，提供预计时长后可查询预估总价。

## v0.9.8 更新

- 文本生成保存异步回执，支持超时、中断后的原任务接续和节点回填。
- 同机、同系统用户的 CLI 按站点和画布排队写入，生成任务继续并行；不同画布互不等待。浏览器和其他电脑仍受服务端版本校验保护。
- 明确的提交拒绝保留具体原因，不再标为结果不确定；ZIP 下载及音频裁剪失败返回非零进程退出码。
- 升级保留登录凭据和原有任务、批次、计划记录。回执权限和素材审核状态需要配套服务端更新，网页图片归档与保存提示不属于此安装包。

## 排队与超时恢复（v0.9.7）

提交前保存请求编号，排队期间先返回 `queueJobId`；媒体任务随后取得 `taskId`。文本返回 `completionId` 和 `resultText`，`completionId` 不是媒体任务 UUID，不能用于 `task-get`。超时、中断或重启后使用原 `runFile` 执行 `task-resume`，不会自动再次提交生成。服务端需开放回执查询权限，仅升级 CLI 不能修复旧服务端的 403。

### 文本生成与接续

`text-generate` 默认等待结果并保存 `.mira/runs/` 记录。`--timeout 0` 仅检查一次，生成仍在后台进行时返回退出码 3 和接续命令。正文也保留在兼容字段 `choices[0].message.content`；不把模型思考过程当作结果。

```sh
python mira.py text-generate --file text.json --confirm-spend --run-file text-run.json --timeout 120 --project-id "画布UUID" --node-id "文本节点ID"
python mira.py task-resume --run-file text-run.json --timeout 120
```

回填目标必须为文本节点；不指定目标则仅返回正文。回填失败时保留已生成正文，接续只重试回填；用户已修改内容时停止覆盖。文本回执可加入批次跟踪，不属于图片→视频执行计划。旧文本请求没有本地记录时，不能凭空用 `task-resume` 找回；先核对原排队编号，不重新提交来“重试”。

## 白模转换（v0.9.6）

1. `python mira.py white-model-options` 检查当前站点是否开放。
2. `python mira.py white-model-quote --json '{"durationSeconds":3}'` 查询参考积分；无法估价不代表免费。
3. 将已有视频上传后，运行 `video-white-model --json '{"videoUrl":"https://example.com/source.mp4","parameters":{"durationSeconds":3}}' --confirm-spend --wait --project-id <画布ID> --node-id <结果节点ID>`。请替换为实际可用地址，并先确认费用。
4. 中断后使用返回的 `runFile` 执行 `task-resume --run-file <文件>`，避免重复提交。原视频节点保留，结果回填到指定的结果节点。上游未公开风格或多人锁定参数。

## 素材集合与同步修复（v0.9.5）

- 错误输出保留服务端的 `detail`、`message` 或 `error` 原因，包括未归档、素材无权限及校验失败。
- `asset-search` 返回的是资产集合。先用 `asset-contents` 展开，选择 `assets[].importId` 导入；`person:...` 不能直接作为文件 ID。`scope` 沿用搜索结果。最新服务已关闭旧全局 `shared` 入口，请使用个人库；团队文件通过现有团队入口选用到个人库后，可按文件 ID 导入。导入时重新检查权限，移出团队后不能继续导入该团队来源的文件。
- 新任务和新制作计划忽略素材自动登记字段的变化；提示词、模型、参考图地址、仅靠 ID 指定的素材及任务占用仍有冲突保护。旧回执沿用原校验规则；旧记录若仍误报，核对节点后用 `task-wait --task-id <已有任务ID> --project-id <画布ID> --node-id <节点ID> --run-file <新回执.json>` 挂回已有结果，不重新生成。
- 画布列表在页面前台时每 5 秒静默刷新已加载内容，保留搜索和目录。页面隐藏或离开列表时暂停后台读取；短暂断网保留原卡片。

```powershell
python mira.py asset-search --json '{"category":"portrait","scope":"personal"}'
python mira.py asset-contents --json '{"item_id":"person:<搜索返回的ID>","scope":"personal"}'
python mira.py asset-import --json '{"asset_id":"<assets 中选定文件的 importId>"}'
```

## 中断与恢复（v0.9.3）

- 批量执行边提交边跟踪，每个任务独立查询；短暂超时、限流和服务端临时错误在等待预算内退避重试，只查询或回填，不重发生成。权限错误、真实生成失败、用户内容冲突仍需处理。
- `production-run` 收到 Ctrl+C 后停止后续提交，返回退出码 130；已提交的服务器任务不取消。使用原计划接续。运行期间可用 `production-status` 只读查询进度。
- Windows 回执读取允许原子替换，短暂文件占用会有界重试。新计划和新任务记录允许移动、缩放节点；修改提示词、模型、任务或素材仍触发冲突保护。旧记录保留原指纹校验。
- 回填后核对当前节点结果，幂等响应不会直接被视为成功。确定未提交生成时清理本次节点占用；清理暂时失败可用原记录 `task-resume` 再尝试，提交结果未知时绝不自动解锁或重新生成。

## 生成与批次可靠性（v0.9.2）

- 图片、视频、音频生成即使不加 `--wait`，也会先在 `.mira/runs/` 保存记录，再提交一次；立即返回的回执保留原任务响应，并增加 `runFile`、`resume`、`phase`。使用 `task-resume --run-file ...` 接续，不要重复执行生成。
- 指定目标节点时，CLI 会先按画布版本占用节点。已有未结束任务、另一终端正在提交、或提交结果尚未确认时，换记录文件也不能重复生成。收到明确任务 ID 前的断网会保留占位和记录，需核对网站任务，再用 `task-wait --task-id ...` 关联已有结果；不会自动重发生成。
- 多图任务完整回填 `resultUrls`；旧版只回填一张的记录可以用 `task-resume` 补齐，不重新生成。只返回部分预览图时不会报告完整回填成功。
- `batch-status` 的进程退出码与 JSON 一致：0=全部完成，3=仍有任务待处理，4=存在失败或需要人工核对。`--summary`、`--save-response`、`--fields` 不改变退出状态。

创建画布时 `canvas-create` 的 `title` 必填，不能为 null、空字符串或纯空白。AI 应根据创作主题取简洁明确的名称，例如“草原与森林｜治愈短片”，不要把整段需求当作标题。画布列表直接展示该名称。漏填会在发送请求前报错，`--skip-validation` 也不能绕过命名要求。

下列命令中的 `python` 指安装了 requirements.txt 的虚拟环境解释器。原有 `--json`、`--file`、`--confirm-spend` 保持使用；默认仍输出完整接口 JSON。

## 浏览器登录

```powershell
python mira.py login
# 只需要查询时，可主动限制为只读
python mira.py login --scope read
python mira.py account
```

`login` 自动打开当前 `MIRA_URL` 的 Mira 授权页。未登录先完成网页登录，核对终端授权码后点击「确认授权」。CLI 自动把有效期 7 天的令牌保存到系统凭据库，不需要复制粘贴；网页和终端都不显示访问令牌。默认一次申请 `generate` 完整创作权限，包含读取、编辑、上传和生成，无需逐项重新登录；可用 `--scope read` 或 `--scope edit` 主动限制权限。授权本身不扣费，生成仍需要 `--confirm-spend`。

可选参数：`--no-browser` 仅显示链接；`--timeout 120` 设置等待秒数（1–300，默认 300）；`--label "我的电脑"` 设置插件页的客户端名称。链接 5 分钟有效，只能领取一次。取消、过期或校验失败不会主动删除原有本机凭据。

`auth-save` 保留手动输入令牌；旧服务端不支持浏览器登录时可继续使用。`auth-clear` 只清除本机凭据，撤销远程访问请到 Mira 插件页。若环境中设置了 `MIRA_TOKEN`，它仍优先于系统凭据库。远程 `MIRA_URL` 必须使用 HTTPS，本机可使用 `http://127.0.0.1:8091`。

## 从分镜到批量执行

`production-create` 从已应用的布局文件整理每镜参数；`production-preflight` 整批校验并报价；`production-run` 在费用授权后边提交、边跟踪并回填，全部提交后默认继续等待最多600秒；`production-select` 记录审图选择；`production-resume` 接续超时或中断的已有任务。`production-status` 查询进度。完整示例及费用控制见 [执行计划指南](EXECUTION_GUIDE.md)。

## 模型规则、批次与验收

新增 `generation-check` 查询当前站点的版本化模型规则；图片/视频提交可加 `--check-model`。新增 `batch-create`、`batch-status`、`batch-resume` 管理已有任务，只接续、不重新生成。新增 `media-inspect` 检查本机成品的实际技术规格。完整示例、未知规则处理和退出码见 [生产流程指南](PRODUCTION_GUIDE.md)。

## 批量分镜布局

用 `storyboard-plan --example` 获取镜头列表示例，填写后运行：

```powershell
python mira.py storyboard-plan --project-id <画布UUID> --file shots.json --out plan.json
python mira.py storyboard-apply --file plan.json
```

第一步只读画布并保存布局预览，第二步新增节点和连线，不执行生成。支持说明→图片→视频、图片→视频、说明→图片三个模板，最多10镜，自动避开已有节点。重复应用同一计划不重复创建，版本冲突不自动覆盖。输入字段、模板和重试规则见 [分镜布局说明](STORYBOARD_GUIDE.md)。

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

任务摘要包含任务 ID、状态、进度、结果地址、错误和积分结算信息。结果地址只读取服务端公开字段，不从内部元数据中找回被审核隐藏的地址。

用 `--summary --fields "taskId,billing.status,billing.chargedPointsExact"` 查看结算。已结算时，`chargedPointsExact` 是账本确认的精确积分。钱包账号显示实际扣费，内部账号显示积分用量。`pending` 表示待结算，`unavailable` 表示暂时查不到结算信息。保留原任务 ID 稍后查询即可。文本生成用回执里的 `billingTaskId` 查询，接续原记录也会刷新结算。旧服务端可能没有该字段。

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

适用于 `image-generate`、`video-generate`、`audio-generate`。文本生成当前仍为同步调用，不使用这组任务等待选项。不带等待选项仍立即返回，但从 v0.9.2 起会自动保存可接续记录。

生成时等待，并把结果放回已经存在的媒体节点：

```powershell
python mira.py image-generate --file image.json --confirm-spend --wait --project-id "画布UUID" --node-id "图片节点ID" --timeout 600 --run-file cat-run.json
```

参数文件仍只放生成参数。`--project-id`、`--node-id` 是 CLI 选项，必须成对出现。CLI 会在提交前检查节点存在、节点类型与生成类型一致。请先通过 `generation-quote` 查看报价；等待功能不会替你确认消费。

Seed Audio 1.0 报价使用 `{"type":"audio","model":"seed-audio-1.0"}`，返回 `creditsPerSecond`（每秒积分）。如果能估计成品时长，可加 `"durationSeconds": 5` 获取预估总积分。实际用量按上游返回的原始成品时长结算。

只等待结果，不回填：

```powershell
python mira.py video-generate --file video.json --confirm-spend --wait --timeout 600 --run-file video-run.json
```

指定回填目标或 `--run-file` 也会启用等待。默认每 5 秒查询一次，可用 `--poll-interval` 调整为 1–60 秒；默认等待预算为 600 秒，`--timeout 0` 只检查当前状态。预算针对提交后的轮询，提交和回填还有独立的网络请求时间，不是整个命令的总耗时上限。

提交前会在 stderr 输出请求编号和记录路径；stdout 最终输出一份 JSON 回执。服务端开启队列时，先返回 `queueJobId`，生成任务创建后再取得 `taskId`；尚无 `taskId` 不代表提交失败。记录文件默认保存在当前目录 `.mira/runs/<随机ID>.json`，也可以显式指定 `--run-file`。记录包含站点、请求编号、排队编号、任务 ID、画布和节点 ID、回填操作 ID、结果地址及执行状态，不保存访问令牌和生成参数。不要把私人任务记录提交到公共仓库。

超时、中断或回填失败后，使用原记录接续：

```powershell
python mira.py task-resume --run-file cat-run.json --timeout 600
```

`task-resume` **不会重新生成**：先查询原任务的服务端状态；已成功的任务只进行回填或确认已回填。即便进程在回填成功后丢失响应，也会识别节点上已有的相同结果。同一记录只能由一个 CLI 进程使用。

排队超过 30 秒、提交响应丢失或进程中断后，CLI 使用已保存的请求编号找回原回执，再跟踪生成结果；`batch-resume` 和执行计划接续也支持此流程。查询返回 403/404 或服务端标记结果待核对时，保留原记录，不会自动再次提交。旧记录若既没有任务 ID，也没有请求或排队编号，需要先在站点核对原任务，取得任务 ID 后使用 `task-wait`。

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


## 节点布局与连线

画布连线从上游节点右侧输出，接入下游节点左侧。创建有依赖关系的节点时，优先从左向右分层，并让同组节点的显示中心对齐；不要把图片与对应视频放在同一列后再连接，否则会产生折返曲线。间距应按实际显示宽高计算，考虑空白媒体节点在页面初始化时可能自动调整尺寸；仅按写入的 width/height 排版后，还应检查页面中的实际连线路径。批量更新布局只修改 x/y，不覆盖提示词、素材与任务状态。


## 节点目录与流程查询（v0.5.2）

新增离线命令 node-list、node-info --node image、workflow-list、workflow-info --workflow nature-video。先选合适的可见节点，再读取输入输出与参数映射；这些查询无需令牌、不联网、不提交生成。node-info --example 可输出创建节点的 JSON 示例，支持 --fields 与 --save-response。详见 [节点与流程指南](NODE_GUIDE.md)。目录明确标出外部剪辑边界，不解锁前端隐藏工具。


## 节点下载、图片 ZIP 与音频裁剪（v0.9.4）

仅覆盖当前画布开放的媒体操作，不新增隐藏工具。需要配套更新服务端；旧服务端没有节点下载路由。

```powershell
# 按任务下载；支持本地归档与 Mira 对象存储，不依赖旧签名 URL
python mira.py task-download --json '{"task_id":"任务UUID","output":"./result.mp4"}'
# 下载节点当前展示结果（图片、视频、音频）；result_index 可选，从 0 开始
python mira.py node-download --json '{"project_id":"画布UUID","node_id":"image-1","output":"./result.png"}'
python mira.py node-download --schema
python mira.py node-download --example
# 两张图片打包，也可用 --group-id 分组ID 或 --all-images
python mira.py images-download --project-id 画布UUID --node-id image-1 --node-id image-2 --output ./images.zip
# 多结果图片节点可加 --all-results；默认只下载当前展示的那张
python mira.py images-download --project-id 画布UUID --group-id 分组ID --all-results --output ./group.zip
# 先试听：只下载和本机裁剪，不上传、不修改画布
python mira.py audio-trim --project-id 画布UUID --node-id audio-1 --start 0.5 --end 4.5 --run-file ./trim.json --preview-only
# 去掉 --preview-only：上传裁剪结果，在右侧创建音频节点并连回原节点
python mira.py audio-trim --project-id 画布UUID --node-id audio-1 --start 0.5 --end 4.5 --run-file ./trim.json
```

- 所有下载都不覆盖已有文件。单文件最多 512 MB；网络错误、中断会删除本次未完成文件。节点下载须有可访问的 Mira 归档、已上传参考素材或有权限的素材记录；外部链接、blob/IndexedDB 临时素材须先上传。
- ZIP 一次最多 100 张图片，使用编号防止同名覆盖，并含 manifest.json（节点、结果序号、文件名、字节数、SHA-256）。任意图片失败时退出码 4，返回失败节点，不保存残缺 ZIP。
- 音频裁剪必填 project-id、node-id、start、end、run-file。时间单位为秒，起点非负，终点不能超过源时长，至少保留 0.05 秒。需要 PATH 中可调用 ffmpeg 和 ffprobe。
- run-file 必须为 .json，试听文件为同目录同名 .wav。原音频保持不变。发布 WAV 受当前画布 15 MB 音频上传限制，超限仍保留本机试听文件。
- 使用相同站点、节点、区间和 run-file 接续，上传成功后只重试回填；已完成时返回原结果节点，不重复创建。画布 409 冲突会重读版本；源音频更换后停止，避免处理过期素材。
- 下载和试听需要读取权限；上传、回填还需编辑权限。本流程不提交图片、视频或音频生成，不需要 --confirm-spend。
