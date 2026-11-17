# 分镜批量布局

`storyboard-plan` 读取现有画布，生成可以审阅的 JSON 布局计划；`storyboard-apply` 一次新增节点和连线。不会调用模型、提交生成或消耗生成积分。已有节点、素材和连线保持原样。

## 三种模板

| template | 每个镜头的节点 | 每个镜头的连线 |
| --- | --- | --- |
| `storyboard`（默认） | 说明、图片、视频 | 说明 → 图片 → 视频 |
| `image-video` | 图片、视频 | 图片 → 视频 |
| `image-board` | 说明、图片 | 说明 → 图片 |

每次 1–10 个镜头，按输入顺序逐行排列。最多 30 个节点、20 条连线，对应服务端单批最多 50 个操作，整批原子应用。只使用当前开放的文本、图片、视频节点，没有新增前端按钮或开放隐藏工具。

## 输入示例

将下面内容保存为 UTF-8 的 `shots.json`：

```json
{
  "template": "storyboard",
  "defaults": {"aspectRatio": "16:9", "duration": 10},
  "shots": [
    {
      "title": "草原晨光",
      "description": "开场，明亮通透、真实自然",
      "imagePrompt": "清晨阳光照亮草原，露水闪烁，远处森林和山峦，自然纪录片摄影",
      "videoPrompt": "镜头缓慢向前移动，微风吹动草叶，保持原图构图和自然光线"
    },
    {
      "title": "林间溪流",
      "imagePrompt": "明亮森林中的清澈溪流，阳光穿过树冠，真实自然摄影",
      "videoPrompt": "固定机位，溪水缓慢流过石头，树叶轻轻摇动"
    }
  ]
}
```

每镜必填 `title`、`imagePrompt`。可选 `description`、`videoPrompt`，以及覆盖 defaults 的 `aspectRatio`、`duration`、`imageModel`、`videoModel`。不接受未知字段。

- `aspectRatio`：`16:9`（默认）、`9:16`、`1:1`，支持同批不同画幅。
- `duration`：整数 4–30 秒，默认 10。这里只是配置，是否被具体模型支持须在生成前检查。
- `imageModel` / `videoModel`：可选模型 ID，先通过 `model-list --kind image/video` 查询。未填写时不强行写入模型，前端采用其默认选择。
- `videoPrompt` 未填写时使用 `imagePrompt`；建议明确描述运镜和物体运动。
- 视频配置为首帧模式（界面值 `keyframe`）、720p、24fps、关闭声音生成及自动时长；这不代表所有模型均支持这些参数。
- `image-board` 没有视频，duration 不参与总时长。

## 使用步骤

下列命令以 `python mira.py` 为例，也可使用现有 `mira` 启动命令。

```powershell
python mira.py storyboard-plan --example
python mira.py storyboard-plan --schema
python mira.py storyboard-plan --file shots.json --validate-only
python mira.py storyboard-plan --project-id <画布UUID> --file shots.json --out plan.json
python mira.py storyboard-apply --file plan.json
```

前三个命令离线运行。第四个命令只读画布，不修改；默认只输出节点数量、总时长、每镜节点 ID、行列位置及计划路径。完整节点、提示词、连线和操作参数保存在 `plan.json`，可以审阅。第五个命令才真正新增节点。

默认将新内容放在现有节点下方，保持间距；媒体节点按前端的 350px 短边规则计算大小，每行节点中心对齐，连线从左往右。需要指定区域时，计划命令同时提供 `--x 80 --y 2000`，与现有节点重叠会报错。不会自动移动已有节点。

计划文件不覆盖已有文件。调整镜头内容时修改 `shots.json`，再生成一个新的计划文件。不要手改计划里的 actions、节点 ID 或 operationId。

## 版本变化与重试

- 计划绑定当前站点、画布和版本；换站点时拒绝应用。
- 应用前再次读取画布；提交保留计划中的 baseVersion，服务端遇到冲突返回 409，CLI 不强行覆盖或自动重排。
- 明确收到 409 后，用最新画布重新生成计划文件并审阅。
- 网络中断、不确定是否应用成功时，使用**同一份计划文件**重试 `storyboard-apply`。稳定的 operationId 让服务端返回已完成回执，不重复创建；不要通过重新规划来重试不确定结果。
- 已应用后，即使其他节点发生更新，重复应用仍由服务端幂等处理。它不会恢复被用户后来删除或修改的节点。

布局完成不等于生成完成。后续仍须读取节点、显式组装参数、验证、报价，再执行生成与结果回填；节点连线不会自动传参或调用模型。
