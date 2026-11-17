"""Offline, versioned guidance for the currently visible canvas nodes.

This module is documentation, never an executor or a server capability oracle.
"""
from copy import deepcopy

REVISION = '2026-11-17'
PROJECT = '11111111-1111-4111-8111-111111111111'
COMMON = {
    'execution': '创建节点或连线不会执行生成；CLI 不会自动读取节点配置或沿连线收集素材。必须显式读取、映射参数、提交。',
    'versions': '编辑前 canvas-get 获取最新 version；canvas-apply 提供 baseVersion 与 operationId。409 后重读；同一次编辑重试沿用 operationId。',
    'layout': '右侧输出→左侧输入；上游放左、下游放右，同组显示中心对齐。按实际显示尺寸留间距，空白媒体卡可能自动调整尺寸；不要把关联图片和视频放在同一列。',
    'references': 'add_connection 的 sourceId/targetId 是节点 ID；它们不是生成接口所需的素材 URL。读取上游 metadata.src/resultUrl，必要时走素材认证，再显式放入生成请求。',
    'update': 'update_node 的 metadata 应合并当前 metadata 后写入；只移动位置时仅更新 x/y。不要把旧快照整个覆盖回去。',
    'spending': '查询目录、schema、--validate-only 不生成；实际生成前查询报价并获得费用授权，使用 --confirm-spend。估价不可用不代表免费。',
    'runtime': '本目录是安装包内的说明快照，不是当前账号权限或模型实时能力。model-list/model-info 提供公开信息，模型专属未知限制需确认。',
}

NODES = {
 'text': {
    'label':'文本', 'purpose':'创作说明、分镜、提示词、文字讨论与结果记录',
    'useWhen':['记录已有文字，不需要模型生成','需要模型协助写作或整理分镜'],
    'inputs':['手写 content','生成时提供 messages；模型或参考资料需显式传入'],
    'outputs':['metadata.content 文本；不是媒体文件'],
    'commands':['canvas-get','canvas-apply','text-generate'],
    'parameterMapping':{'metadata.title':'节点名称','metadata.content':'展示文字；不会自动变成 messages','metadata.textManualEntry':'手写文本设为 true','显式选择的文本模型 ID':'先查 model-list，将选定 ID 作为 model；不要假定节点保存了模型字段'},
    'execution':{'directCommand':'text-generate','automaticAttachment':False,'steps':['手写文字直接 canvas-apply','AI 写作：准备 messages，先验证及报价，再 text-generate','读取实际响应中的文字，合并当前 metadata 后更新 content；没有文本 --wait 自动回填']},
    'limits':['不要为了记录分镜调用付费写作；创建 text 节点即可'],
 },
 'image': {
    'label':'图片', 'purpose':'构图、首帧、视觉参考及基于原图的修改',
    'useWhen':['需要先确认视觉方向','需要修改已有图而保留构图'],
    'inputs':['prompt','可选参考图 URL 或 URL 数组 image'],
    'outputs':['生成任务 ID','完成后的 resultUrl、metadata.src，可作为视频首帧'],
    'commands':['canvas-get','canvas-apply','image-generate','task-resume'],
    'parameterMapping':{'metadata.prompt':'prompt','metadata.imageModel':'model','metadata.numImages / imageCount':'numImages','metadata.imageCustomWidth + imageCustomHeight':'width/height，并显式填写 size="宽x高"','上游图片 metadata.src/resultUrl':'image；连线不会自动传参'},
    'execution':{'directCommand':'image-generate','automaticAttachment':True,'steps':['读取节点和上游素材','显式构造请求并 --validate-only','generation-quote','授权后 image-generate --wait --project-id ... --node-id ... --run-file ...','中断或超时使用 task-resume；生成成功但回填失败只重试回填']},
    'limits':['imageAspectRatio 是界面配置，不是 image-generate 的独立字段；需转成模型支持的 size','n 优先于 numImages；size 优先于 width/height，不要传入互相矛盾的值','修改已有图片时先保存原结果，并将原图 URL 作为 image 参考'],
 },
 'video': {
    'label':'视频', 'purpose':'生成单个动态镜头，或展示导入的剪辑成片',
    'useWhen':['构图确认后生成动态镜头','导入外部完成的剪辑结果'],
    'inputs':['prompt','图生视频 firstFrameImage','多模态 referenceImages/referenceVideos/referenceAudios，受模型限制'],
    'outputs':['视频任务 ID','完成后的 resultUrl、metadata.src'],
    'commands':['canvas-get','canvas-apply','video-generate','task-resume','task-download','asset-upload'],
    'parameterMapping':{'metadata.prompt':'prompt','metadata.videoModel':'model','metadata.videoAspectRatio':'aspectRatio','metadata.videoDuration':'duration','metadata.videoResolution':'resolution','metadata.videoFps':'fps','metadata.videoGenAudio / generateAudio':'generateAudio','上游图片 metadata.src/resultUrl':'firstFrameImage（图生视频）','metadata.videoMode':'界面值不等于 API mode，按实际输入选择 text_to_video/image_to_video/first_last_frame/multimodal'},
    'execution':{'directCommand':'video-generate','automaticAttachment':True,'steps':['先确认首帧和运镜，再构造请求','验证、报价、费用授权','video-generate --wait 指定目标和独立 run-file','task-download 后核对实际分辨率、时长、音轨；不能把请求值当作成品规格']},
    'limits':['视频节点不等于剪辑器，连接多个视频不会自动拼接','当前已核对的本机版本中 Seedance 2.5 只采用480p/720p，1080p请求会被降为720p；其他站点或后续版本需重新核对','CLI 未提供公开的时间线运行或转场混音命令；需要外部剪辑时明确说明，再 asset-upload 回填现有可见视频节点'],
 },
 'audio': {
    'label':'音频', 'purpose':'音频生成入口、音乐/环境声素材与播放节点',
    'useWhen':['导入许可清楚的已有配乐','调用当前站点开放的音频服务'],
    'inputs':['已有音频文件或素材','audio-generate 的 text_prompt 与可选 references'],
    'outputs':['音频 URL 或生成任务；可供支持该输入的视频模型引用'],
    'commands':['asset-upload','canvas-apply','audio-generate','task-resume'],
    'parameterMapping':{'metadata.prompt':'text_prompt（不是 prompt）','metadata.src / audioUrl':'导入或生成的可播放音频 URL','references':'按 audio-generate --schema 构造，不能假定是 URL 字符串数组'},
    'execution':{'directCommand':'audio-generate','automaticAttachment':True,'steps':['已有音频：asset-upload，再将返回 url/fileKey 写入 audio 节点','生成音频：先核对服务用途、schema 和费用；不要仅凭 audio 名称假定支持任意作曲','需要生成时显式提交 text_prompt，--wait 回填 audio 节点']},
    'limits':['音乐检索、许可核对、裁切与混音不是创建音频节点后自动发生的功能','audio 节点的连线不等于已经混入最终视频；成片需实际混音并检查音轨'],
 },
}

WORKFLOWS = {
 'nature-video': {
    'label':'约一分钟自然风景视频','purpose':'从分镜、构图到镜头和配乐成片',
    'nodes':['text','image','video','audio'],
    'steps':[
      {'stage':'规划','commands':['storyboard-plan','storyboard-apply'],'action':'准备 shots 列表，先 storyboard-plan 保存预览，再 storyboard-apply 创建说明→图片→视频布局。最多10镜，不提交生成；重试沿用同一份计划。'},
      {'stage':'构图','commands':['model-list','generation-check','image-generate','generation-quote'],'action':'先看 --help/--schema 并 generation-check；确认模型规则检查范围，报价与授权后生成图片，--check-model --wait 回填。'},
      {'stage':'审图','commands':['canvas-get','image-generate'],'action':'检查色调、构图和真实感。需要修改时传原图 image，保存原版本；只修改需要调整的镜头。'},
      {'stage':'视频','commands':['generation-check','video-generate','generation-quote','batch-create','batch-status','batch-resume','task-download'],'action':'显式把图片 URL 放入 firstFrameImage，逐镜指定运镜。模型规则检查、报价与授权；每任务独立记录，批次接续已有任务，不重提交生成。'},
      {'stage':'配乐与剪辑','commands':['asset-upload','canvas-apply'],'action':'寻找并核对音乐许可。当前公开 CLI 不支持转场混音执行；说明采用外部剪辑（如 FFmpeg），完成后上传并放入 video 节点。勿创建隐藏节点绕过限制。'},
      {'stage':'验收','commands':['media-inspect','canvas-get'],'action':'media-inspect 对下载成品核对实际时长、尺寸、帧率和音轨；再观看、试听检查画面与声音质量。确认画布回填，记录音乐署名。'},
    ],
    'pitfalls':['连接素材不等于提交生成','不要照搬节点的 videoMode 值','报价前确认模型实际支持的分辨率','只查询或补挂已有任务时不能再次调用生成'],
 },
 'image-revision': {
    'label':'保留构图修改图片','purpose':'改善光照、色调或局部布局', 'nodes':['image'],
    'steps':[
      {'stage':'备份','commands':['canvas-get','task-download'],'action':'读取当前节点并保存原图，明确要保持与要修改的部分。'},
      {'stage':'编辑','commands':['image-generate','generation-quote'],'action':'原图 URL 放入 image，写修改提示词，验证和报价；授权后提交一次并 --wait 回填。'},
      {'stage':'复核','commands':['canvas-get','canvas-apply'],'action':'比较修改前后；同步下游视频提示词，避免仍引用旧光线或旧物体数量。'},
    ],
    'pitfalls':['不带 image 就是重新文生图，不能保证保留原构图','自动重试不得重复提交付费生成'],
 },
 'import-reference': {
    'label':'导入素材并供下游引用','purpose':'使用本机文件或账号资产', 'nodes':['image','video','audio'],
    'steps':[
      {'stage':'获取素材','commands':['asset-upload','asset-search','asset-import'],'action':'本机文件用 asset-upload；已有资产先搜索再导入，读取实际返回的 URL 和素材信息。'},
      {'stage':'展示与连接','commands':['canvas-get','canvas-apply'],'action':'使用可见媒体节点的 metadata.src 和对应 fileKey，建立 sourceId/targetId 连线并从左到右布局。'},
      {'stage':'用于生成','commands':['material-preparations','material-authenticate','video-generate'],'action':'按实际模型要求准备素材；上传成功不等于审核通过。显式把有效素材地址放入对应生成字段，再验证和报价。'},
    ],
    'pitfalls':['不要把浏览器 blob: URL 当作服务端素材 URL','不要复制旧素材的审核标志到新素材','保留本机与所连接站点的区别，localhost URL 不是通用公网链接'],
 },
}


def creation_example(kind):
    metadata={'title':NODES[kind]['label']+'示例','status':'idle'}
    if kind=='text':metadata.update(content='创作说明与分镜',textManualEntry=True)
    else:metadata['prompt']='填写创作提示词'
    node={'id':'example-'+kind,'type':kind,'x':80,'y':120,'width':640,'height':360 if kind!='audio' else 138,'metadata':metadata}
    return {'project_id':PROJECT,'baseVersion':0,'operationId':'replace-with-unique-operation-id','actions':[{'type':'add_node','payload':{'node':node}}]}


def query(operation, identifier=None, example=False):
    collection=NODES if operation.startswith('node-') else WORKFLOWS
    if operation.endswith('-list'):
        key='nodes' if collection is NODES else 'workflows'
        return {'catalogRevision':REVISION,'offline':True,'scope':'已核对的可见基础节点；不包含隐藏入口，不保证远端当前版本一致',
                key:[{'id':k,'label':v['label'],'purpose':v['purpose']} for k,v in collection.items()]}
    if identifier not in collection:
        raise ValueError('目录未公开该条目。可用 ID：'+', '.join(collection))
    if example:return creation_example(identifier)
    result={'id':identifier,'catalogRevision':REVISION,'offline':True,**deepcopy(collection[identifier]),'rules':deepcopy(COMMON)}
    if collection is NODES:result['createExample']=creation_example(identifier)
    return result
