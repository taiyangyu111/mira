# 模型规则、批量接续与成品验收（CLI v0.7.0）

以下命令使用 `python mira.py`，也可以换为已安装的 `mira` 启动命令。查询与验收不提交生成；`batch-resume` 可能更新已绑定的画布节点，但不会重新生成。

## 1. 当前站点的模型规则

```powershell
python mira.py model-info --kind video --model seedance25 --fields id,capabilities
python mira.py generation-check --kind video --file video.json
```

`generation-check` 先执行通用校验，再读取当前站点的公开模型目录和版本化规则，返回具体错误字段、已检查范围、未知项以及规则 revision，不创建任务。通用参数格式错误时不联网。

例如 Seedance 2.5 的 1080p 请求会在本机当前服务端被改成720p；检查现在会指出 resolution 不符合公开规则。还检查已核实的视频时长范围、参考素材数量、输出格式和仅音频参考限制。

注意区别：`--validate-only` 仍是**离线通用校验**，不会读取实时模型规则。`generation-check` 中 `valid=true` 只表示已检查项通过，应同时看 `modelChecked`、`coverage` 和 `notChecked`。

本版规则覆盖当前 Seedance 2.0/2.5 服务端处理逻辑；图片目前公开配置中的分辨率档位及通用数量、宽高边界，模型专属像素、参考图片数量等未知限制明确保留为未知，不保证任意参数可用。声音生成、素材审核、余额、上游实时可用性仍须单独确认。

实际提交时可开启严格模型检查：

```powershell
python mira.py video-generate --file video.json --check-model --confirm-spend --wait --run-file shot-01.json --project-id <画布UUID> --node-id <视频节点ID>
```

上述命令会生成并可能扣费，只能在报价、授权后执行。`--check-model` 在规则不通过、模型未匹配或旧服务端未提供规则时停止，不提交生成。它不能与 `--validate-only` 或 `--skip-validation` 同用。没有开启它的原有调用行为保持兼容。

新模型规则随**服务端版本**提供。仅升级 CLI 无法让旧站点自动获得规则；旧站点会明确返回未检查状态，而不是把未知解释为支持。`parameters` 扩展参数可能覆盖顶层字段，不属于本版完整校验范围。

## 2. 多个已有任务组成批次

使用已有 `--run-file` 记录，不需要迁移或重新生成：

```powershell
python mira.py batch-create --name 草原影片 --run-file shot-01.json --run-file shot-02.json --out film-batch.json
python mira.py batch-status --file film-batch.json --summary
python mira.py batch-status --file film-batch.json --refresh --summary
python mira.py batch-resume --file film-batch.json --timeout 600 --workers 3 --summary
python mira.py batch-resume --file film-batch.json --attachments-only --summary
```

- 每批1–100份记录，按站点绑定，拒绝重复路径和覆盖已有批次文件。批次保存任务记录的绝对路径；移动电脑或文件后须重新组批。
- `batch-status` 默认读取本地记录，`--refresh` 查询服务端状态，并核对已成功任务是否仍挂在目标节点，两者均不改记录、不回填。默认完整返回每个任务，`--summary` 只显示计数及需要处理的任务。
- 区分 `completed`、`running`、`generation_failed`、`attach_failed`、`ready_to_attach`、`query_failed`、`needs_attention`。任务成功不等于已经挂回节点。
- `batch-resume` 复用原任务 ID、回填目标和回填 operationId。默认 `--timeout 0` 每任务只检查一次；设置等待时间后逐轮处理所有未完成任务，默认3个并行查询/回填，最多8个。
- 已完成、生成失败、提交结果未知或预检失败的任务不重复生成，也不反复轮询。生成失败需要人工判断是否创建一个新的付费任务；没有任务 ID 时先核对服务端记录。
- `--attachments-only` 只处理本地记录中已生成成功但回填待处理/失败的任务。不会为尚未完成的任务提前挂结果。回填遇到用户修改仍沿用单任务冲突保护。
- 单条记录损坏、被占用或服务查询失败会单独报告，不阻断其他任务。等待结束后沿用同一个批次文件接续。
- 终端退出后不会继续在后台运行。单次检查仍有网络超时；`--timeout` 是等待预算，不是精确的进程强制退出期限。

`batch-status` 成功读取汇总后进程退出码为0；报告的 `exitCode` 表示批次状态。`batch-resume` 使用该退出码：0全部完成，3仍有待处理任务，4存在失败或需要人工处理。

## 3. 本机成品验收

需要安装 FFmpeg 中的 `ffprobe`，放入 PATH，或通过 `--ffprobe` / `MIRA_FFPROBE` 指定路径。本命令只读本机文件，不自动下载网络素材。

```powershell
python mira.py media-inspect --file final.mp4
python mira.py media-inspect --file final.mp4 --expect-width 1280 --expect-height 720 --expect-duration 57 --expect-fps 24 --expect-audio yes
python mira.py media-inspect --file final.mp4 --expect-duration 60 --duration-tolerance 4 --save-response acceptance.json
```

报告实际宽高、时长、帧率、编码与音轨，并逐项比较。`--expect-audio no` 可检查是否不含音轨。时长容差默认1秒，帧率容差默认0.1；只有明确提供的预期才参与验收。

无预期时 `passed=null`，表示仅探测；满足全部预期时为 true；不满足或对应实际值未知时为 false，退出码2。媒体无法读取或缺少 ffprobe 时退出码1。不要把“有音轨”当作有可听音乐；画面质量、全片逐帧可解码性、音乐内容和授权仍需观看、试听或另外检查。

## 精简输出与保存

`generation-check`、`batch-status`、`batch-resume`、`media-inspect` 均支持 `--fields` 和 `--save-response`。保存完整报告，再筛选终端输出，不覆盖已有文件。只保存时输出文件回执；批次指定 `--summary` 时仍输出摘要。批次接续前预留报告文件，避免目标路径错误发生在回填之后。
