# 批量执行计划（CLI v0.8.0）

从已经应用的分镜布局，建立「构图图 → 审图选择 → 首帧视频」的可审阅执行计划。自动整理每镜节点 ID、提示词、模型、参数和报价，复用现有任务等待、回填和批量接续机制。

本版每镜一张构图图，视频为固定时长首帧模式，不包含自动剪辑、混音、音乐授权检查或无人值守审美判断。`image-board` 模板只执行图片阶段。生成可能扣费；所有查询、预检报价、选图和已有任务接续均不提交新生成。

## 1. 创建可审阅计划

先完成 `storyboard-plan` / `storyboard-apply`，保留原来的布局文件。随后：

```powershell
python mira.py production-create --layout layout.json --out film.json --image-model seedream5.0 --video-model seedance25
python mira.py production-status --file film.json
python mira.py production-preflight --file film.json
```

- `--layout` 是布局计划文件，不是原始 shots.json；所有对应节点必须已存在。
- 读取**当前节点**的提示词、模型、时长等。两个 model 参数仅在节点没配置模型时补充，不偷偷挑选模型。
- 默认按画幅取图片尺寸：16:9 为2560×1440，9:16为1440×2560，1:1为2048×2048。节点明确配置自定义尺寸时沿用；可用 `--image-size 2048x2048` 统一指定实际尺寸。混合画幅时不建议统一指定尺寸。
- 图片数量必须为1。视频沿用节点的时长、分辨率、画幅、帧率和声音配置；本版不接受参考模式或自动时长。
- 创建命令只读画布、不改配置，计划文件不覆盖。打开 film.json 可审阅完整参数和目标节点。
- 计划发现已有成功素材会复用，发现已有 taskId 会要求先 `production-resume` 接续，不会替它再生成一次。失败任务需要另行判断，新版本应使用新节点或另建计划。

## 2. 整批预检和报价

`production-preflight` 检查图片、视频两阶段的参数和当前模型规则，逐镜报价，给出 `quoteId`、图片/视频分别的预计积分及合计。完整检查报告存入执行计划，终端默认输出摘要。

视频没有选定首帧时标为 `waiting_for_selection`：此时按「一张首帧」暂估价格，未选择的引用只用于本地参数检查，不会作为假素材提交生成。选择后必须重新预检。

任一新步骤参数错误或报价不可用都会阻止批量生成。报价不可用不是免费，完整合计为 null；`knownSubtotalExact` 仅表示已知部分。已有任务和素材不计入新的待提交报价。

图片模型的一些专属限制仍未公开，模型检查不是完整成功保证；详见每步 `check.coverage` 和 `check.notChecked`。预检不验证余额或执行付费素材审核。

## 3. 获得费用授权后执行图片阶段

```powershell
python mira.py production-run --file film.json --stage images --quote-id <图片阶段已审阅的报价ID> --max-points <允许的报价合计> --confirm-spend
python mira.py production-resume --file film.json --stage images --timeout 600 --workers 3
```

`production-run` **可能扣费**。提交前再次整批预检：报价 ID 必须对应已审阅结果，15分钟后过期；模型规则、请求参数或价格发生变化时停止并要求重新预检。首次付费前确认全部待提交报价在 `--max-points` 内，每个任务前再核对目标和即时价格。

`--max-points` 限制本次阶段中尚未提交任务的**报价合计**，不是服务端实际结算的硬上限。只有对新报价与任务范围授权后才执行；不能把预检通过当作费用授权。

任务逐个提交，每个只做一次初始状态检查，然后使用 `production-resume` 继续等待。每镜在网络调用前保存独立任务记录；运行过程中不确定提交结果、生成失败、查询或回填失败时停止剩余新提交，不会自动重新生成。

## 4. 审图、选择首帧，再执行视频

先在画布中查看图片，必要时另行修改。确认后记录选择：

```powershell
python mira.py production-select --file film.json --shot 01
python mira.py production-select --file film.json --all
python mira.py production-preflight --file film.json
python mira.py production-run --file film.json --stage videos --quote-id <新报价ID> --max-points <视频阶段允许的报价合计> --confirm-spend
python mira.py production-resume --file film.json --stage videos --timeout 600
```

`--all` 表示全部当前图片已经审阅，不是自动评价图片质量。也可用 `--shot 01 --source-node <同画布其他图片节点ID>` 选择另一张图；只记录执行输入，不自动调整画布连线，使用另一节点时须同步检查连线展示。

选图只写入本地计划，不生成、不改图片；清空旧报价。视频必须全部选定首帧后才能批量启动。首帧地址或任务身份后来改变会使选择失效，须重新审图选择；签名 URL 刷新不会被当成图片变化。视频已经有任务或素材时不允许再改变该步骤的输入。

## 5. 中断、冲突与交付

```powershell
python mira.py production-status --file film.json
python mira.py production-resume --file film.json --stage images --timeout 600
python mira.py production-resume --file film.json --stage videos --timeout 600
```

- `production-resume` 只接续已有任务，不启动未提交步骤。`production-run` 才能提交新的付费任务。
- 重复 run 会跳过已有任务记录；同阶段尚未提交的镜头，仍需要有效报价和费用授权。未知提交结果必须先核对服务端，不能删除记录后重试。
- 回填失败仍复用已有结果；用户改变了目标节点时沿用单任务冲突保护，不覆盖用户修改。报价后修改目标配置或布局也会阻止新生成，需要重新整理计划。
- 创建新的计划也不会自动重复已有节点上的任务：任务 ID/现有素材会被识别。不要通过清空节点的任务信息绕过防重逻辑。
- 执行记录放在计划目录的 `.mira/productions/<计划ID>/`。计划与此目录须一同保留，不能只移动 film.json。文件锁阻止两个 CLI 同时执行同一计划。
- 每镜状态返回 taskId 和 runFile，可直接使用 `task-download` 下载，再使用 `media-inspect` 验收实际规格。当前不会自动拼接或交付整部影片。
- 终端关闭后不继续在后台执行；重新打开后接续原计划即可。

所有 production 命令支持 `--fields` 和 `--save-response`，可将完整报告保存到新文件。退出码：0操作成功；1参数/文件/报价授权条件错误；预检不通过为2；运行遇到需要处理的问题为4；接续仍有任务未结束时为3。
