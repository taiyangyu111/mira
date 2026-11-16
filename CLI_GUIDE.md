# CLI 参数与精简输出（v0.2.0）

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

更新时替换整个包并重新安装 requirements.txt；不要只复制 mira.py。CLI 现在还需要 mira_cli_support.py、mira_contracts.json 和 jsonschema 依赖。
