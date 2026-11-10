# comfyui-hervinai-none

让 ComfyUI 的 **Load Image / Load Audio / Load Video** 支持**空输入**。

文件下拉框不选任何文件时，节点不再抛异常，而是把所有输出置为 `None`。
这样可以把加载节点安全地接在条件工作流里：没有素材的支路直接断开，
而不会中断整条流水线。

**核心节点完全不受影响**——本包不覆盖、不修改 `LoadImage` / `LoadAudio` /
`LoadVideo`，只是新增了三个独立节点。删掉本目录就等于卸载。

![ComfyUI](https://img.shields.io/badge/ComfyUI-Custom%20Node-3b82f6)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 特点

- **空输入不报错**：不选文件时输出 `None`，而不是抛异常中断整条流水线
- **默认就是空**：节点拖到画布上不会自动填入第一个文件
- **不碰核心节点**：只新增 `*Hervinai` 三个节点，核心的 `LoadImage` /
  `LoadAudio` / `LoadVideo` 行为完全不变
- **预览不丢**：图片、视频、音频三种预览都保留
- **零新依赖**：只用 ComfyUI 核心已有的 numpy / torch / Pillow / av
- **视频节点额外输出**：`IMAGE`（抽出的帧）+ `AUDIO`（音轨），
  并带一组 VHS 风格的帧处理参数

---

## 节点一览

| 节点名（搜索用） | 显示名 | 分类 | 输出 |
|---|---|---|---|
| `LoadImageHervinai` | Load Image Hervinai 空输入安全图像 | `hervinai-none` | `IMAGE`, `MASK` |
| `LoadAudioHervinai` | Load Audio Hervinai 空输入安全音频 | `hervinai-none` | `AUDIO` |
| `LoadVideoHervinai` | Load Video Hervinai 空输入安全视频 | `hervinai-none` | `VIDEO`, `IMAGE`, `AUDIO` |

在节点搜索框输入 `Hervinai` 即可找到。

> `LoadVideoHervinai` 除了 `VIDEO`，还额外输出 `IMAGE`（视频抽出的帧）
> 和 `AUDIO`（视频的音轨），并带一组 VHS 风格的帧处理参数（帧率 / 尺寸 / 抽帧），
> 详见下面「视频抽帧输出 IMAGE」「音轨输出 AUDIO」与「帧处理参数」三节。

---

## 安装

### 方式一：ComfyUI Manager（推荐）

在 ComfyUI Manager 里搜 `comfyui-hervinai-none`，点 Install。

### 方式二：git clone

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/henry19801109/comfyui-hervinai-none.git
```

### 方式三：手动下载

从 [Releases](https://github.com/henry19801109/comfyui-hervinai-none/releases) 下载
源码包，解压到 `ComfyUI/custom_nodes/comfyui-hervinai-none/`：

```
ComfyUI/
└── custom_nodes/
    └── comfyui-hervinai-none/
        ├── __init__.py
        ├── hervinai_none_nodes.py
        ├── requirements.txt
        ├── web/
        │   └── hervinai_none.js
        └── README.md
```

装好后**完全重启** ComfyUI（前端扩展需要重新加载，刷新浏览器页面不够）。
控制台出现 `[hervinai-none]` 相关日志即表示加载成功。

本包**不引入任何新依赖**，只用 ComfyUI 核心已有的 numpy / torch / Pillow / av。

---

## 行为说明

### 什么算"空输入"

以下取值一律视为空输入，直接输出 `None`，不报错：

- `None`
- 空字符串 `""`
- 纯空白 `"   "`
- 占位项 `"none"` / `"NONE"`（不分大小写）

### 默认就是空的

三个节点拖到画布上时，文件下拉框**默认是空的**，不会自动挂上 input
目录里的某个文件。

实现上这个"空"必须同时满足两个条件，缺一不可：

1. 下拉框的**选项列表第一位是 `none`**（后端插进去的），并且 `default` 指向它；
2. **选中值就是 `none`**（前端设的）。

原因是前端 `WidgetSelect` 组件的显示规则：选中值能在 `options.values`
里匹配到就显示该项的 label，匹配不到就显示原值。如果只设选中值而
不把 `none` 放进选项列表，前端会认为这是个"文件已丢失"的选项，
反而回退显示第一个真实文件。

> 核心 `LoadVideo` / `LoadAudio` 的选项列表里**本来就没有** `none` 占位项，
> 所以本包必须自己补上。

### 正常选文件时

行为与核心节点**完全一致**，包括文件存在性校验。
选了文件但文件已从磁盘删除、或文件损坏导致解析失败时，
会降级为输出 `None` 并在日志里打一条 warning，而不是让整个任务崩掉。

### 行为确认

不选上传文件时，三个节点的输出**确实都是 `None`**：

| 输入 | 输出 |
|---|---|
| `none`（占位项） | `(None, None)` / `(None,)` / `(None, None)` |
| `None` | 同上 |
| `""`（空串） | 同上 |
| 选了真实文件 | 返回真实数据 |

### 视频抽帧输出 IMAGE

`LoadVideoHervinai` 除 `VIDEO` 外还多一路 `IMAGE` 输出，
可以直接接到 `VAE Encode`、`Save Image` 等节点上。

**取帧路径与官方 `Get Video Components` 完全一致。**
核心把 `VIDEO` 拆成 `IMAGE` / `AUDIO` 的官方节点是 `GetVideoComponents`，
它走的是 `video.get_components().images`。本包沿用**同一条**路径，
所以像素格式、旋转角（rotation metadata）、10bit / HDR 的处理
都与核心一致，不会出现"颜色对不上"或"方向歪了"。

抽出来的张量形状是 `[帧数, 高, 宽, 通道]`，`float32`，取值 `0~1`——
与 ComfyUI 通用的 `IMAGE` 类型一致。

视频里一帧都解不出来时（空文件 / 损坏），核心会返回 `torch.zeros(0,0,0,3)`
这样的空张量；本包会把它统一转成 `None`，语义上与"抽不出帧"保持一致，
不会让下游拿到一个 0 帧的怪 IMAGE。

**`max_frames` 控制抽帧上限。**
节点上多了一个可选输入 `max_frames`，默认 `16`：

| 值 | 含义 |
|---|---|
| `16`（默认） | 最多抽 16 帧 |
| `0` | **不限**——解出视频的全部帧 |
| 其它数值 | 最多抽这么多帧 |

> **为什么默认是 16 而不是全部。**
> 一帧 1080p 的 RGB float32 是 `1920 × 1080 × 3 × 4 ≈ 24 MB`。
> 一个 10 秒 30fps 的视频就是 300 帧 ≈ **7.4 GB**——
> 直接解全部帧几乎必然爆内存。
> 16 帧约 380 MB，是绝大多数机器都扛得住的量。

实现上，限制帧数时会先用 `as_trimmed()` 把时间窗口裁短，
让核心的解码器**只解这么多帧就停**，而不是解完再截断。
连续窗口对视频编码最友好（顺序解码），比逐帧随机 seek 快得多。

> 抽帧是**额外**的开销：即使你只接 `VIDEO` 输出，节点也仍然会抽帧。
> 这是因为 V3 节点不支持"输出级惰性求值"，
> 一个节点的所有输出是在同一次执行里一起算出来的。
> 如果视频很长又不需要 `IMAGE`，可以把它上面的 `max_frames` 调小
> （比如 `1`），或者干脆用核心的 `Load Video` 节点。

**改了 `max_frames` 会重新执行。**
核心的指纹只看文件修改时间，所以本包把 `max_frames` 的值也并进了指纹，
否则改了抽帧数 ComfyUI 会认为"没变化"而跳过执行。

### 音轨输出 AUDIO

`LoadVideoHervinai` 的第三路输出是 `AUDIO`——视频里的声音，
可以直接接到 `Save Audio`、`Preview Audio` 等节点上。

**音轨跟随抽帧窗口截取。**

也就是说，音轨不是整条，而是和抽出来的画面**同一个时间窗口**：

| 节点参数 | 对音轨的影响 |
|---|---|
| `skip_first_frames` | 音轨从这一帧对应的时刻开始 |
| `max_frames` | 音轨长度 = 抽帧窗口时长 |
| `force_rate` | 不影响音轨（只改变画面的时间轴密度） |
| `select_every_nth` | 不影响音轨（只改变抽帧的疏密） |

举例：30fps 的视频，`max_frames=16`、`skip_first_frames=0`，
音轨就是开头 `16/30 ≈ 0.533` 秒。
`max_frames=60` 则音轨变成开头 2 秒。

**实现上为什么天然就对齐。**
核心的 `get_components()` **一次解码**就同时给出画面和声音，
而且声音在核心内部就已经按这个窗口裁好了
（`get_components_internal` 里先按 `to_skip` 跳过开头，
再按 `int(duration * sample_rate)` 截断尾部）。

所以本包**不为音轨单独再解一遍**——画面和声音来自同一次解码，
既省一半时间，又保证两者严格对齐、绝不会错位。
这与 VHS 的做法一致（VHS 也是
`lazy_get_audio(video, start_time, frame_load_cap * 帧时长)`）。

**音轨的格式**与核心 `Load Audio` 输出一致：

```
{
    "waveform": torch.Tensor,   # 形状 [B, 通道数, 采样数]，float32，取值 -1~1
    "sample_rate": int          # 采样率
}
```

**视频没有音轨时，这一路输出 `None`**（不是空张量，也不会报错）。
无音轨的视频在核心里 `components.audio` 本来就是 `None`，
本包原样透传。

> **降级**：万一你用的核心版本没有 `io.Audio`（AUDIO 输出是后加的），
> 节点会自动只保留 `VIDEO` + `IMAGE` 两路输出，**不会**导致整个包加载失败。

### 帧处理参数（VHS 风格）

除了 `max_frames`，节点上还有一组 VideoHelperSuite 风格的帧处理参数：

| 参数 | 默认 | 含义 |
|---|---|---|
| `force_rate` | `0` | 强制帧率。`0` = 保持原帧率；其它值 = 把帧序列重采样到这个帧率 |
| `custom_width` | `0` | 目标宽度。`0` = 保持原始宽度 |
| `custom_height` | `0` | 目标高度。`0` = 保持原始高度 |
| `skip_first_frames` | `0` | 跳过开头的帧数 |
| `select_every_nth` | `1` | 隔几帧取一帧。`1` = 全取 |

**这些参数作用于 IMAGE 与 AUDIO 两路输出，VIDEO 输出保持原视频不变。**
这样画布上的视频预览还在——重建视频会丢原始音轨、
预览还得重新编码，代价太大。

> 所以 `VIDEO` 和 `IMAGE` 描述的是同一个视频，但**帧可能不是一一对应**：
> VIDEO 是原视频，IMAGE 是按上面参数处理过的帧序列。
> 需要"两者完全一致"时，用核心的 `Get Video Components` 节点拆 VIDEO。
>
> `AUDIO` 是按抽帧窗口截取的音轨（见上面「音轨输出 AUDIO」），
> 与 IMAGE 那一路的时间窗口一致。

**语义与 VHS 的两处差异：**

1. **不做 8 像素对齐。** VHS 的 `target_size()` 会把宽高对齐到 8 的倍数
   （为了编码器兼容），本包不做——填 1000 就是 1000，
   对齐后变成别的数容易让人困惑。
2. **只填一个维度时保持宽高比**（这点与 VHS 一致）：
   只填 `custom_width` → 高度按原比例算；只填 `custom_height` → 宽度按原比例算；
   两个都填 → 强制拉伸到指定尺寸。

**实现上的三个细节：**

- **处理顺序与 VHS 一致：跳过开头 → 重采样帧率 → 隔帧取 → 帧数封顶。**
  帧率重采样放在隔帧取**之前**，跟 VHS 里 `fps=` 滤镜的位置相同。
- `skip_first_frames` 和 `select_every_nth` 在**抽帧阶段**就处理掉了，
  不是解完再丢。这样解码器只会解需要的帧——比如要 10 帧、隔帧取 3，
  就只解码 30 帧的窗口，而不是解完整个视频再丢掉 95%。
- `force_rate` 用的是"按时间戳就近取帧"：降帧率时丢帧，
  升帧率时重复已有帧（源视频里本来就没有那么多帧）。
  重采样**按目标帧数精确产出**，所以解码窗口解多解少都不影响最终帧数；
  `max_frames` 也仍然能压住重采样后的帧数。

**改任何一个参数都会重新执行。**
这些参数的值都并进了指纹。

### 关于下游

节点自身输出 `None` 后，**下游节点是否报错取决于下游**。
大多数下游节点收到 `None` 会抛出类型错误，这是 ComfyUI 通用行为，
本包不做额外拦截。常见做法是：

- 用 `If` / `Impact Switch` 之类的节点做分支，让 `None` 走另一条路；
- 或者在链路中放一个接受任意类型输入的透传节点做占位。

---

## 实现要点

### 双路径：核心的 V1 与 V3 两套节点写法

新版 ComfyUI 已把 `LoadVideo` / `LoadAudio` 迁移到 V3 schema
（`io.ComfyNode`），而 `LoadImage` 仍是 V1。两套写法的关键差异：

| | V1 | V3 |
|---|---|---|
| 输入描述 | `INPUT_TYPES()` 类方法 | `define_schema()` 返回 `io.Schema` |
| `INPUT_TYPES` | 可覆盖 | **`@final`，不能覆盖** |
| 执行入口 | `FUNCTION` 指向实例方法 | `FUNCTION` 是 classmethod `EXECUTE_NORMALIZED` |
| 校验钩子 | `VALIDATE_INPUTS` | `validate_inputs`（小写） |
| 指纹计算 | `IS_CHANGED` | `fingerprint_inputs` |

所以本包按核心节点的实际风格走两条路：

- **V1 路径**：继承核心类，覆写 `INPUT_TYPES` 把 `none` 插到选项首位、
  覆写 `VALIDATE_INPUTS` 在空输入时放行、覆写 `IS_CHANGED` 在空输入时
  跳过哈希，再包一层 runner 把空输入转成 `None` 输出。
- **V3 路径**：继承核心类，覆写 `define_schema` 注入占位项、
  覆写 `execute`（classmethod）把空输入转成 `io.NodeOutput(None, ...)`、
  覆写 `validate_inputs` 与 `fingerprint_inputs`。

### 几个容易踩的坑

- **`INPUT_TYPES` 在 V3 上是 `@final`**：不能按 V1 的方式覆写它，
  否则要么启动报错，要么改不动。想加占位项只能从 `define_schema` 入手。
- **V3 的 `FUNCTION` 是 `EXECUTE_NORMALIZED`**：它内部调用
  `cls.execute(**kwargs)`，注意是 **classmethod**，不是 `core_func(self, ...)`。
  空输入判断必须写在 `execute` 的覆写里。
- **V3 的 `CATEGORY` / `display_name` / `node_id` 都是 `@final` 的
  classproperty**，值只能从 `schema` 里来。不改的话节点会显示成
  "Load Video"、挂在核心的 `video` 分类下，前端补丁按分类名识别
  本包节点就会失效。本包在 `define_schema` 里显式改写这三项。
- **`Combo.Input` 不重建，只原地改**：重建需要照抄构造函数的参数名，
  而不同版本的 ComfyUI 参数并不一样（新版多了 `raw_link` / `advanced`），
  照抄某一个版本的签名换到另一个版本上就会抛 `TypeError`，
  进而让整个节点注册失败。原地修改 `options` 和 `default` 两个属性
  则与版本无关。
- **指纹计算要先短路**：`fingerprint_inputs`（V3）/ `IS_CHANGED`（V1）
  在 `execute` **之前**被调用，会对文件做哈希或取修改时间。空输入时
  文件根本不存在，直接调用会抛异常并在日志里刷一片警告。本包在
  空输入时返回常量。
- **V3 基类上的 classproperty 缓存不能被继承**（这个坑很隐蔽，
  表现是 `list index out of range`）：
  核心的 `_ComfyNodeBaseInternal` 有一批带缓存的属性：

  ```python
  _RETURN_TYPES = None
  @final
  @classproperty
  def RETURN_TYPES(cls):
      if cls._RETURN_TYPES is None:
          cls.GET_SCHEMA()      # 首次访问时算出来并缓存
      return cls._RETURN_TYPES
  ```

  核心的 `comfy_extras` 先于 `custom_nodes` 加载，所以核心 `LoadVideo`
  的 `_RETURN_TYPES` 已经被填成 `["VIDEO"]`。我们继承核心类建的子类
  **没有自己的** `_RETURN_TYPES`，属性查找沿 MRO 向上走 → 读到核心那份。

  于是前后端看到的东西不一致：

  | | 数据来源 | 看到的输出 |
  |---|---|---|
  | 前端（`object_info`） | `GET_NODE_INFO_V1()` → `schema.outputs` | VIDEO + IMAGE + AUDIO ✅ |
  | 后端校验 | `RETURN_TYPES`（继承来的缓存） | 只有 VIDEO ❌ |

  ComfyUI 的校验代码按 slot 索引取上游节点的输出类型：

  ```python
  r = nodes.NODE_CLASS_MAPPINGS[o_class_type].RETURN_TYPES
  received_type = r[val[1]]        # 前端连的是 slot 1 → IndexError
  ```

  前端按两个输出连线、后端以为只有一个 → 越界，
  报 `Exception when validating node: list index out of range`。

  **修法**：建子类时把这些缓存属性在自己的 `__dict__` 里重置为 `None`，
  让它按自己的 `define_schema` 重算。重置的是子类自己的属性，
  **核心的缓存完全不受影响**（不会污染核心自己的 `LoadVideo`）。
  本包在 `_reset_v3_caches()` 里统一处理，覆盖
  `_RETURN_TYPES` / `_RETURN_NAMES` / `_OUTPUT_IS_LIST` /
  `_OUTPUT_TOOLTIPS` / `_CATEGORY` / `_DESCRIPTION` 等 16 个属性。

### 其它

- **文件字段名自动探测**：三个核心节点的文件字段名并不一致——
  Load Image 叫 `image`，Load Audio 叫 `audio`，
  而 **Load Video 叫 `file`**。本包在加载时从核心类的输入定义里
  识别带 `*_upload` 标记的字段来定位真实字段名，不写死任何名字，
  核心改名也不会失效。
- **V3 的上传标记不是属性，是动态生成的**：
  核心的 `Combo.Input` 并不把 `audio_upload` 存成属性，
  而是在 `as_dict()` 里**现算**：

  ```python
  def as_dict(self):
      return super().as_dict() | prune_dict({
          ...
          **({self.upload.value: True} if self.upload is not None else {}),
      })
  ```

  所以想摘掉 `audio_upload`，光 `setattr(item, "audio_upload", False)`
  是**没用的**——真正的开关是 `self.upload`。
  本包把它置空，标记就不会生成。
- **新增输入必须从 kwargs 里剔掉再传给核心**：
  核心的 `execute` / `validate_inputs` / `fingerprint_inputs`
  都只认 `file` 这一个参数。多传 `max_frames` 会直接 `TypeError`。
  本包在构建节点时记下所有附加输入的 id，调用核心前统一过滤掉。
- **附加输入的值必须并进指纹**：
  核心的指纹只看文件修改时间。改了 `max_frames` 却不改指纹的话，
  ComfyUI 会认为"没变化"而跳过执行，改了参数没反应。
- 核心类缺失时（裁剪版安装）自动回退到内置最小实现，
  保证本包不会因为"找不到核心类"而整体加载失败。
- 通过 `NODE_CLASS_MAPPINGS` / `NODE_DISPLAY_NAME_MAPPINGS` 两个模块级变量注册，
  这是 ComfyUI 加载自定义节点包的约定入口。

### 前端补丁（web/hervinai_none.js）

核心前端为视频 / 音频节点提供了预览播放器和上传框，但有两处需要补。

**1. 默认值**

视频 / 音频的文件控件在节点创建时会**异步**拉取 input 目录的文件列表，
然后自行把选中值设成列表里的第一个文件，把我们的设置覆盖掉。

补丁用**「用户交互探测 + 轮询回正」**接管这一步：

- 挂钩控件的 `setValue` 与 `callback`，一旦发现写进来的是**非空值**，
  就认定这是用户主动选的（在下拉框里挑文件、或点了上传），
  立刻结束保护窗口，从此以用户的选择为准；
- 保护窗口内每 100ms 扫一次：若控件值非空、但用户还没主动操作过，
  说明是前端异步自动填的，就拉回 `none`。

**为什么不直接拦截 `value` 写入。**
前端 `ComboWidget.setValue` 的执行顺序是

```js
this.value = a;              // 先写值
this.callback?.(a, ...);     // 再回调
```

如果用 `defineProperty` 拦截赋值，用户在保护窗口内刚点完下拉框选好文件，
写入就会**当场被吞掉**——等 callback 里再补救已经晚了，用户看到的就是
"选了文件但没反应"。同理，点上传按钮时写入的文件名也会被吞掉。
所以补丁只观察、不拦截。

另外补丁会确保 `none` 确实在 `options.values` 里且排在第一位——
后端已经做了一遍，这里再补一次是为了覆盖"后端是旧版本 /
前端先于后端拿到数据"的情况，也能顺带修掉核心把占位项排在中间的情况。

**2. 音频播放器 + 上传按钮**

核心前端有**两个**音频扩展，各管一头：

```js
// Comfy.AudioWidget —— 按**节点名**硬编码注入播放器
if (['LoadAudio','SaveAudio','PreviewAudio','SaveAudioMP3',
     'SaveAudioOpus','SaveAudioAdvanced'].includes(comfyClass))
    input.required.audioUI = ['AUDIO_UI', {}]

// Comfy.UploadAudio —— 按**输入标记**注入上传按钮
if (input.required.audio[1].audio_upload === true)
    input.required.upload = ['AUDIOUPLOAD', {}]
```

两个条件**各管一头**，改名后的节点就掉进了夹缝：

- 节点名 `LoadAudioHervinai` 不在白名单里 → 核心不给它注入 `audioUI` 输入
  → 画布上**没有** `audioUI` 控件；
- 但后端继承了核心 `LoadAudio` 的 `audio_upload` 标记 → 核心给它注入了
  `upload` 输入 → 画布上**有** `AUDIOUPLOAD` 控件。

而 `AUDIOUPLOAD` 控件的构造函数**第一件事**就是找那个不存在的 `audioUI`：

```js
AUDIOUPLOAD(node) {
  const audioUI = node.widgets?.find(w => w.name === 'audioUI');  // undefined
  const update = () => updateUIWidget(audioUI, url);  // audioUI.element → 崩
  update();   // 立刻执行
}
```

`updateUIWidget` 第一行就是 `widget.element.src = url`，于是抛

```
TypeError: Cannot read properties of undefined (reading 'element')
    at updateUIWidget (core-*.js)
    at onAudioWidgetUpdate (core-*.js)
    at AUDIOUPLOAD (core-*.js)
    at addInputWidget (app-*.js)
    at addInputs (app-*.js)
    at new ComfyNode (app-*.js)
```

节点**直接加载不上画布**（搜索框里找得到，拖上去就报错）。

**修法**：不让核心注入 `AUDIOUPLOAD`，播放器和上传按钮改由本补丁自建。
前端 `disableCoreAudioUpload()` 与后端摘标记**双管齐下**，
对两种扩展执行顺序都成立：

| 执行顺序 | 谁负责处理 |
|---|---|
| 核心扩展先跑 | 前端删掉已被注入的 `upload` / `audioUI` 输入 |
| 本扩展先跑 | 后端 + 前端摘掉 `audio_upload` 标记，核心就不会注入 |

补丁在 `onNodeCreated` 里直接建控件：

- 播放器：`node.addDOMWidget("audioUI", "audioUI", <audio controls>)`，
  播放地址由 `audio` 组合框的值实时推导（`/view?filename=...`）；
- 上传按钮：`node.addWidget("button", "upload", ...)`，
  走 `POST /upload/image`（ComfyUI 统一上传接口），成功后把新文件塞进下拉列表。

**播放器控件名必须叫 `audioUI`，而且 name 和 type 都是。**

这一点很关键，因为核心在**执行完之后**还会按名字找这个控件来刷新播放器：

```js
// Comfy.AudioWidget.onNodeOutputsUpdated
for (const [id, out] of Object.entries(outputs)) {
  if (!out.audio?.length) continue;
  const node = findNode(id);
  const widget = node.widgets?.find(w => w.name === 'audioUI');  // 找不到 → 崩
  updateUIWidget(widget, url);
}
```

本节点输出 AUDIO，所以这段每次执行都会跑。控件名对不上就会抛
`TypeError`（虽然被 `invokeExtensions` 的 try/catch 吞掉，但会刷一片控制台错误）。

核心自己的 `AUDIO_UI` 控件也是这么建的：

```js
const widget = e.addDOMWidget(name, 'audioUI', element);   // name 和 type 都是 audioUI
```

所以 `audioUI` **不是**「保留标识」，而是核心约定的名字——照着用即可。

> 反过来说，核心的 `AUDIOUPLOAD` 控件是在**节点构造函数**里创建的
> （`addInputs` → `addInputWidget`），那时本补丁建的控件还不存在，
> 所以 `upload` 输入**必须**删掉，绝不能让核心来建这个控件。
> 这两件事是配套的：控件用 `audioUI` 命名（让核心执行后能找到它），
> 但 `upload` 输入要删掉（让核心别在构造函数里建那个会崩的控件）。

**为什么不设置 `node.previewMediaType = "audio"`。**

`previewMediaType` 是前端判断「这个节点要渲染哪种预览」的标记，
音频节点被识别后会走 `isAudioNode()` 那条专门的渲染分支，
而那条分支是按核心节点名硬编码匹配的（`useMoreOptionsMenu` 里写死了
`LoadAudio` / `LoadImage` / `LoadVideo`），改名后的节点匹配不上。
参考实现的 `LoadAudioUI` 同样没有设这个值——它的播放器完全是自己
`addDOMWidget` 建的 DOM 控件，不依赖前端的预览通道。

**为什么不用「往定义里注入 `AUDIO_UI` 输入」那种写法。**

前端渲染节点时，先跑 `addInputSocket` 给每个输入建插槽，
再跑 `addInputWidget` 找对应控件。只有**已注册的控件类型**才会被
`addInputWidget` 认领；而 `addInputSocket` 对**未知类型**照样会建一个插槽
（只是没有控件绑定）。也就是说，往定义里塞一个没注册过的类型名
（比如 `["AUDIO_UI", {}]`），就会留下一个悬空插槽，节点渲染时报错，
**直接加载不上画布**。在 `onNodeCreated` 里建控件则完全不碰定义，
天然没有这个问题。

> 两个新增控件都显式设了 `serialize = false`，不写进 `widgets_values`，
> 也就不会把后面控件的下标顶掉一位。
> 注意：只写在 `addWidget` 的 options 里是不够的——前端判断「要不要序列化」
> 看的是**顶层** `widget.serialize` 属性，不会从 `options.serialize` 提升。

视频播放器不受影响：它是按**输出类型 `VIDEO`** 渲染的，与节点名无关。

**只有 `audio_upload` 被摘掉，`image_upload` / `video_upload` 保留。**

核心的上传按钮（`useImageUploadWidget()`）是靠 `image_upload` /
`video_upload` 标记才会创建的，而且它还要求 `imageInputName` 存在：

```js
function isImageUploadInput(e) {
  let t = e[1];
  return t !== void 0 && typeof t === 'object'
      && 'imageInputName' in t && typeof t.imageInputName === 'string';
}
// 构造时：
if (!isImageUploadInput(n)) throw Error('Image upload widget requires imageInputName augmentation');
```

所以这三个标记里**只有 `audio_upload` 是有问题的**，
摘掉它会让图片 / 视频节点退化成只有下拉框、没有上传按钮。

**3. 加载已有工作流**

补丁通过 `onConfigure` 读取 `widgets_values` 识别真实文件名，
识别到就解除保护并还原，不会把你工作流里已选的文件清空。

`widgets_values` 只收录 `serialize !== false` 的控件，
而 `node.widgets` 收录全部控件，所以取下标时要按"可序列化控件序列"算，
不能用 `node.widgets` 的原始下标。

### 排查

启动后控制台会打印本包实际使用的字段名与节点风格，例如：

```
[hervinai-none] LoadVideoHervinai 使用文件字段：file（V3 schema）
[hervinai-none] LoadImageHervinai 使用文件字段：image（V1）
```

如果这里显示的字段名与预期不符，说明核心节点结构变了，
把这一行日志贴出来即可定位。

前端补丁是否加载成功，看控制台有没有这行：

```
[hervinai-none] 前端补丁已加载： LoadVideoHervinai
```

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包入口，导出 `NODE_CLASS_MAPPINGS` / `NODE_DISPLAY_NAME_MAPPINGS`，并通过 `WEB_DIRECTORY` 注册前端目录 |
| `hervinai_none_nodes.py` | 后端节点实现（V1 / V3 双路径） |
| `web/hervinai_none.js` | 前端补丁：默认空值 + 音频播放器补齐 |
| `requirements.txt` | 无额外依赖，留空 |
| `README.md` | 本文档 |
