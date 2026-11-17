# 给 AI 的画布节点与创作流程指南

目录版本：2026-11-17；Mira CLI v0.5.0。

先查询目录，再选择工具。目录只收录核对过的前端可见基础节点，**不会解锁隐藏工具，也不提供新的生成入口**。这是随 CLI 安装的离线说明，不代表远端站点实时权限；模型和素材状态仍需查询当前服务。

## 按需查询

以下命令不联网、不需要令牌、不消耗生成积分。Windows 用 `.\.venv\Scripts\python.exe`，macOS/Linux 用 `.venv/bin/python`。

```powershell
.\.venv\Scripts\python.exe mira.py node-list
.\.venv\Scripts\python.exe mira.py node-info --node video
.\.venv\Scripts\python.exe mira.py node-info --node image --fields purpose,parameterMapping,execution,limits
.\.venv\Scripts\python.exe mira.py node-info --node image --example --save-response create-image.json
.\.venv\Scripts\python.exe mira.py workflow-list
.\.venv\Scripts\python.exe mira.py workflow-info --workflow nature-video
```

`--save-response` 保存本次查询结果；同时使用 `--fields` 时保存筛选后的结果。文件已存在会报错，不会覆盖。

## 当前可用节点

| 节点 ID | 用途 | 执行与回填 |
| --- | --- | --- |
| text | 创作说明、分镜、文字讨论 | 手写文字直接更新 content；AI 写作用 text-generate，返回文字需显式更新节点 |
| image | 构图、首帧、图片修改 | image-generate；可 --wait 自动回填 |
| video | 单镜头生成、导入成片展示 | video-generate；可 --wait 自动回填；多个视频连线不会自动拼接 |
| audio | 已有音频素材、当前音频服务入口 | 上传已有配乐，或按服务能力 audio-generate；可 --wait 自动回填 |

资产库与上传是素材入口，不是另一种生成节点。使用 `asset-search`、`asset-import`、`asset-upload`。公开目录不列出隐藏的时间线、剧本、3D 等入口，不能通过构造隐藏节点绕过产品限制。

## 一组图片到视频节点的完整创建示例

先用 `canvas-get` 读取当前版本。将下面的 project_id、baseVersion、operationId 换成实际值，保存为 UTF-8 的 `pair.json`；ID 必须在该画布中唯一。

```json
{
  "project_id": "11111111-1111-4111-8111-111111111111",
  "baseVersion": 0,
  "operationId": "replace-with-unique-operation-id",
  "actions": [
    {"type":"add_node","payload":{"node":{"id":"shot-01-image","type":"image","x":80,"y":120,"width":640,"height":360,"metadata":{"title":"01 草原构图","prompt":"真实清晨草原，自然阳光","status":"idle"}}}},
    {"type":"add_node","payload":{"node":{"id":"shot-01-video","type":"video","x":1000,"y":120,"width":640,"height":360,"metadata":{"title":"01 草原镜头","prompt":"缓慢前移，微风吹动草叶，保持光线稳定","status":"idle"}}}},
    {"type":"add_connection","payload":{"connection":{"id":"shot-01-reference","sourceId":"shot-01-image","targetId":"shot-01-video"}}}
  ]
}
```

```powershell
.\.venv\Scripts\python.exe mira.py canvas-apply --file pair.json --validate-only
.\.venv\Scripts\python.exe mira.py canvas-apply --file pair.json
```

通用请求校验不会完整检查 action.payload 中所有节点业务字段，服务端仍会验证。上述操作只创建节点与连接，不生成媒体。

## 从节点到生成请求

1. 用 `canvas-get --node-id 节点ID` 读取目标及相关连线；需要上游内容时单独读取上游节点。
2. 用 `node-info` 查映射，用 `model-list`、`model-info` 查公开模型信息，再用生成命令的 `--help`/`--schema` 查接口字段。
3. 显式构造生成参数。连线只是引用关系，CLI 不会自动沿线读取素材，也没有通用的“运行节点”命令。
4. `--validate-only` 后查询实际参数的报价，获得费用授权，再提交一次。
5. 媒体生成使用 `--wait --project-id ... --node-id ... --run-file ...`。每个新任务用独立记录文件；中断用 `task-resume`，不重复生成。回填冲突时重新读取节点，核对没有新任务或用户修改后，才用 `task-wait` 显式补挂已有任务。
6. 验收实际文件。请求的分辨率、时长不保证就是输出规格。

特别注意：图片的 `imageAspectRatio` 需要转换成生成接口支持的尺寸；视频节点 `videoMode` 的界面值不能直接复制为 API `mode`；音频接口使用 `text_prompt`；文本接口使用 `messages`。

当前已核对的本机服务会将 Seedance 2.5 的 1080p 请求降为 720p。通用 schema 的分辨率字段不等于模型能力保证，应先确认模型实际支持值，再报价和生成。

## 常见流程与外部处理边界

- `nature-video`：文字分镜 → 构图图片 → 审图修改 → 单镜头视频 → 配乐与剪辑 → 上传成片。
- `image-revision`：保存原图 → 原图作为 image 参考 → 定向修改 → 对比并同步下游提示词。
- `import-reference`：本机上传或资产导入 → 媒体节点展示 → 按模型要求认证素材 → 显式传入生成请求。

当前公开 CLI 没有转场、时间线渲染和混音执行命令。需要这些操作时，应明确说明使用外部剪辑工具，例如 FFmpeg；完成后 `asset-upload`，将返回的 `url` 写入可见 video 节点的 `metadata.src`，保留 `fileKey`。**不能把“成片在画布中展示”说成“在画布节点内完成了剪辑”。**

## 排版与素材保护

从左到右按依赖分层；同镜头图片和视频显示中心对齐，成片放右侧。预留实际显示宽高，考虑媒体节点初始化后的尺寸调整，不以保存的 width/height 盲目判断边界。移动只更新 x/y；更新 metadata 时先读取当前 metadata 并合并，保留素材和任务字段。

导入 URL 不等于素材审核通过；不要复制旧素材的审核标志。不要把浏览器 blob: URL 或仅本机可访问的地址当作任意服务都能获取的公网素材。使用已生成结果修改图片时先保存原图，使用原图 URL 作为参考，避免重新文生图偏离构图。
