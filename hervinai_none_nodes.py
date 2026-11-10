"""
comfyui-hervinai-none
=====================

让 ComfyUI 的 Load Image / Load Audio / Load Video 支持"空输入"：
文件下拉框里不选任何文件时，节点不再抛出
"No video selected" / "Invalid image file" 之类的异常，
而是把全部输出置为 None，从而可以安全地挂在条件工作流里，
让某条支路在没有素材时直接断开而不中断整条流水线。

设计要点
--------
1. **不改动 ComfyUI 核心节点**，只新增独立节点；删除本目录即可完全卸载。
2. **同时兼容核心的 V1 与 V3 两套节点写法**。
   新版 ComfyUI 已把 Load Video / Load Audio 迁移到 V3 schema
   （``io.ComfyNode``），它们的 ``FUNCTION`` 是 classmethod
   ``EXECUTE_NORMALIZED``，``INPUT_TYPES`` 是 ``@final``——
   既不能覆盖，也不能按 V1 的 ``core_func(self, ...)`` 方式调用。
   本包因此为 V3 节点单独生成一套"子类化 + 覆写 execute"的实现。
3. **文件字段名自动探测**：核心 Load Video 的字段叫 ``file``，
   Load Image 叫 ``image``，Load Audio 叫 ``audio``，
   本包在构建时从核心类的 ``INPUT_TYPES`` 里自动识别，
   不写死任何字段名，核心改名也不会失效。
3b. **LoadVideoHervinai 额外输出 IMAGE**：除 ``VIDEO`` 外再给一路 ``IMAGE``，
   帧的取值与核心 ``GetVideoComponents`` 完全一致（走同一条
   ``video.get_components().images`` 路径）。抽帧上限由可选输入
   ``max_frames`` 控制（0 = 不限），避免长视频一次性解出全部帧爆内存。
   另外提供一组 VHS 风格的帧处理参数（``force_rate`` /
   ``custom_width`` / ``custom_height`` / ``skip_first_frames`` /
   ``select_every_nth``），**只作用于 IMAGE 输出**——
   VIDEO 那一路保持原视频不变，这样音轨和画布预览都还在。
4. 核心节点类在裁剪版安装中缺失时，自动回退到内置实现，
   保证节点不会因为"找不到核心类"而让整个包加载失败。
5. 空输入的判定：文件名为 None / 空字符串 / 纯空白 / ``none``（不分大小写）。
6. 即使文件名非空但文件在磁盘上已被删除、或文件损坏导致解析失败，
   也会降级为输出 None 并写一条 warning 日志，而不是让整个任务崩掉。
7. **下拉框必须能真正处于"空"状态**：核心 V3 节点的选项列表里
   根本没有 ``none`` 占位项。前端 ``WidgetSelect`` 走的是默认分支
   ``WidgetSelectDefault``，它的显示规则是"选中值能在 ``options.values``
   里匹配到就显示该项的 label，匹配不到就显示原值"，
   ``options.placeholder`` 只是搜索框的提示文案。
   所以要让控件显示为空，**必须同时满足**：``"none"`` 真的在
   ``options.values`` 里（且排在第一位）、选中值就是 ``"none"``。
   只把值设成 ``"none"`` 而不加进选项列表，前端反而会当成
   "文件已丢失"而回退显示第一个文件。
   因此本包在 ``define_schema`` 里原地给 Combo 输入插入空占位项
   并排在第一位，前端补丁再把选中值压成 ``"none"``，
   两边配合才能实现"拖到画布上就是空的"。
"""

import copy
import logging
import os

import folder_paths

logger = logging.getLogger("hervinai-none")

CATEGORY = "hervinai-none"

# 日志前缀，便于在 ComfyUI 控制台里过滤
LOG_TAG = "[hervinai-none]"

# 视为"空输入"的占位字符串（统一转小写比较）
_EMPTY_TOKENS = {"", "none"}

# 核心节点没有自带占位项时，本包使用的占位符字面值。
# 必须是小写的 "none"：前端视频/音频预览扩展靠这个特定字符串
# 判断"当前没有文件"，写成 "None" 前端会当成真实文件名去请求，
# 导致预览空白。
_PLACEHOLDER = "none"

# 用于标记"参数根本没传"的哨兵值
_MISSING = object()

# 节点描述（V1 显示为 DESCRIPTION，V3 显示为 schema.description）
DESCRIPTION = (
    "空输入不报错：文件下拉框未选择文件时，"
    "所有输出为 None，不抛异常。"
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _is_empty(value) -> bool:
    """判断是否属于"空输入"。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_TOKENS
    return False


def _list_input_files() -> list:
    """列出 input 目录下的所有文件（与核心 LoadImage 的做法保持一致）。"""
    try:
        input_dir = folder_paths.get_input_directory()
    except Exception:
        return []
    try:
        return sorted(
            f for f in os.listdir(input_dir)
            if os.path.isfile(os.path.join(input_dir, f))
        )
    except Exception:
        return []


def _import_core_class(module_name: str, attr: str):
    """从指定模块导入核心节点类，失败返回 None。"""
    try:
        module = __import__(module_name, fromlist=[attr])
        return getattr(module, attr, None)
    except Exception as exc:  # pragma: no cover - 取决于安装环境
        logger.warning("%s 无法导入 %s.%s：%s", LOG_TAG, module_name, attr, exc)
        return None


def _core_class(*candidates):
    """按优先级查找核心节点类。

    先查 ComfyUI 的节点注册表（能覆盖改名/迁移的情况），
    再回退到直接导入模块。
    """
    registry = {}
    try:
        import nodes as comfy_nodes  # ComfyUI 核心节点模块
        registry = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}) or {}
    except Exception:
        registry = {}

    fallback_modules = {
        "LoadImage": [("nodes", "LoadImage")],
        "LoadAudio": [("comfy_extras.nodes_audio", "LoadAudio")],
        "LoadVideo": [
            ("comfy_extras.nodes_video", "LoadVideo"),
            ("comfy_extras.nodes_load_video", "LoadVideo"),
            ("comfy_extras.nodes_load_video", "LoadVideoOutput"),
        ],
        "LoadVideoOutput": [("comfy_extras.nodes_load_video", "LoadVideoOutput")],
    }

    for name in candidates:
        cls = registry.get(name)
        if cls is not None:
            return cls
        for module_name, attr in fallback_modules.get(name, []):
            cls = _import_core_class(module_name, attr)
            if cls is not None:
                return cls
    return None


def _is_v3_node(cls) -> bool:
    """判断核心节点是不是 V3 schema（io.ComfyNode）。

    V3 节点的特征：
    - 继承自 ``comfy_api.internal._ComfyNodeInternal``
    - ``FUNCTION`` 是 classmethod ``EXECUTE_NORMALIZED``
    - 用 ``define_schema()`` 描述输入，而不是 ``INPUT_TYPES()``
    """
    try:
        from comfy_api.internal import _ComfyNodeInternal
        if issubclass(cls, _ComfyNodeInternal):
            return True
    except Exception:
        pass

    # 兜底：看 FUNCTION 是不是 EXECUTE_NORMALIZED
    func = getattr(cls, "FUNCTION", None)
    return func in ("EXECUTE_NORMALIZED", "EXECUTE_NORMALIZED_ASYNC")


# ---------------------------------------------------------------------------
# 文件字段名自动探测
# ---------------------------------------------------------------------------

# 上传控件的标识，用来识别哪个输入是"文件选择框"
_UPLOAD_MARKERS = ("image_upload", "audio_upload", "video_upload", "upload")

# 需要被摘掉的标记——**只有音频这一个**。
#
# 核心前端的 Comfy.UploadAudio 扩展看到 ``audio_upload`` 就会注入
# AUDIOUPLOAD 控件，而那个控件需要一个我们拿不到的 ``audioUI`` 控件，
# 构造函数会崩 → 节点加载不上画布。详见 _strip_audio_upload_marker。
#
# 而 ``image_upload`` / ``video_upload`` **必须保留**：
# 核心的 useImageUploadWidget() 靠 ``image_upload`` 标记才会创建上传按钮，
# 摘掉它图片/视频节点就只剩一个光秃秃的下拉框了。
_AUDIO_UPLOAD_MARKER = "audio_upload"


def _detect_file_field(base_cls, preferred: str):
    """从核心节点的 INPUT_TYPES 里找出文件选择框对应的字段名。

    识别顺序：
    1. 带 ``*_upload`` 标记的字段（最可靠）；
    2. 名为 ``preferred`` 的字段；
    3. 第一个"选项是字符串列表"的必填字段。

    这样核心把 ``video`` 改名成 ``file`` 之类的情况也能自动跟上。
    """
    try:
        types = base_cls.INPUT_TYPES()
    except Exception:
        types = None

    if isinstance(types, dict):
        required = types.get("required") or {}
        optional = types.get("optional") or {}
        if isinstance(required, dict):
            # 1) 带 upload 标记的字段
            for name, spec in required.items():
                if isinstance(spec, (list, tuple)) and len(spec) > 1:
                    opts = spec[1]
                    if isinstance(opts, dict) and any(
                        m in opts for m in _UPLOAD_MARKERS
                    ):
                        return name
            # 2) 名字匹配
            for name in (preferred, "file", "image", "audio", "video"):
                if name in required:
                    return name
            # 3) 第一个字符串列表型必填字段
            for name, spec in required.items():
                if isinstance(spec, (list, tuple)) and spec and isinstance(
                    spec[0], (list, tuple)
                ):
                    return name
        if isinstance(optional, dict):
            for name, spec in optional.items():
                if isinstance(spec, (list, tuple)) and len(spec) > 1:
                    opts = spec[1]
                    if isinstance(opts, dict) and any(
                        m in opts for m in _UPLOAD_MARKERS
                    ):
                        return name

    return preferred


def _detect_v3_file_field(base_cls, preferred: str):
    """在 V3 节点的 schema 里找出文件选择框对应的字段名。

    V3 的 ``INPUT_TYPES()`` 是由 schema 反推出来的 V1 兼容视图，
    但 Combo 输入走的是 **V2 风格**：``("COMBO", {options: [...], video_upload: True})``。
    ``*_upload`` 标记放在第二个元素（字典）里，与 V1 一致，
    所以可以直接按标记定位。
    """
    try:
        types = base_cls.INPUT_TYPES()
    except Exception:
        return preferred

    if not isinstance(types, dict):
        return preferred

    for section in ("required", "optional"):
        fields = types.get(section)
        if not isinstance(fields, dict):
            continue
        for name, spec in fields.items():
            if not isinstance(spec, (list, tuple)) or len(spec) < 2:
                continue
            opts = spec[1]
            if isinstance(opts, dict) and any(
                m in opts for m in _UPLOAD_MARKERS
            ):
                return name

    return preferred


# ---------------------------------------------------------------------------
# 核心：空输入安全混入类（用于 V1 节点）
# ---------------------------------------------------------------------------

class _EmptySafeMixin:
    """把"文件为空"的场景从"抛异常"降级为"输出 None"。

    子类需要提供 ``_file_field``（文件输入名，由 ``_detect_file_field`` 探测）。
    """

    _file_field = "image"

    @classmethod
    def INPUT_TYPES(cls):
        """沿用核心节点的输入定义，只在文件下拉框最前面确保有一个占位项。"""
        try:
            types = super().INPUT_TYPES()
        except Exception as exc:
            logger.warning("%s 读取核心输入定义失败：%s", LOG_TAG, exc)
            return {"required": {cls._file_field: ([_PLACEHOLDER], {})}}

        if not isinstance(types, dict):
            return types

        for section in ("required", "optional"):
            fields = types.get(section)
            if not isinstance(fields, dict):
                continue
            spec = fields.get(cls._file_field)
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            options = spec[0]
            if not isinstance(options, (list, tuple)):
                continue  # 形如 ("STRING", {...}) 的普通控件，跳过
            options = list(options)

            # 核心节点的占位项可能不在首位（例如 LoadVideo 的 "none"），
            # 前端默认会选中列表第一项，若不处理，节点一放上去就会
            # 自动带上某个文件。这里把所有占位项剔除，再把空项强制
            # 放到第一位，保证默认就是"空输入"。
            #
            # 占位符的**字面值必须沿用核心的写法**（小写 "none"）：
            # 前端的视频/音频预览扩展靠这个特定字符串判断
            # "当前没有文件"，换成别的值前端就会当成真实文件名去
            # 请求，结果预览一片空白。
            token = None
            for o in options:
                if isinstance(o, str) and o.strip().lower() in _EMPTY_TOKENS:
                    token = o
                    break
            if token is None:
                token = _PLACEHOLDER

            filtered = [
                o for o in options
                if not (isinstance(o, str) and o.strip().lower() in _EMPTY_TOKENS)
            ]

            extra = dict(spec[1]) if len(spec) > 1 and isinstance(spec[1], dict) else {}
            extra["default"] = token
            # 摘掉 audio_upload 标记：核心的 Comfy.UploadAudio 扩展看到它
            # 就会注入 AUDIOUPLOAD 控件，而那个控件需要一个我们拿不到的
            # audioUI 控件（核心只按节点名硬编码注入，改名后匹配不上），
            # 构造函数会抛 "undefined (reading 'element')"，
            # 导致节点加载不上画布。详见 _strip_audio_upload_marker 的说明。
            #
            # 注意只摘 audio_upload：image_upload / video_upload 要保留，
            # 核心的上传按钮靠它们才会出现。
            extra.pop(_AUDIO_UPLOAD_MARKER, None)
            fields[cls._file_field] = ([token] + filtered, extra)

        return types

    @classmethod
    def VALIDATE_INPUTS(cls, *args, **kwargs):
        """文件为空时跳过核心的文件校验；非空时仍然走核心的校验逻辑。"""
        value = kwargs.get(cls._file_field)
        if value is None and args:
            value = args[0]
        if _is_empty(value):
            return True
        parent_validate = getattr(super(), "VALIDATE_INPUTS", None)
        if parent_validate is None:
            return True
        return parent_validate(*args, **kwargs)

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        """空输入时跳过核心的指纹计算。

        核心的 ``IS_CHANGED`` 会对文件做哈希，空输入时文件不存在，
        直接调用会抛异常并在日志里刷警告。
        """
        value = kwargs.get(cls._file_field)
        if value is None and args:
            value = args[0]
        if _is_empty(value):
            return ""
        parent_is_changed = getattr(super(), "IS_CHANGED", None)
        if parent_is_changed is None:
            return ""
        try:
            return parent_is_changed(*args, **kwargs)
        except Exception:
            return ""


def _make_runner(core_func, file_field: str, display_name: str):
    """生成包裹核心执行函数的 runner（V1 风格）。"""

    def _run(self, *args, **kwargs):
        # 区分"没传这个参数"和"传了空值"：
        # 只有真正传了空值才返回 None，参数缺失时交回核心自行处理。
        if file_field in kwargs:
            value = kwargs[file_field]
        elif args:
            value = args[0]
        else:
            value = _MISSING

        if value is not _MISSING and _is_empty(value):
            logger.info("%s %s：未选择文件，输出 None。", LOG_TAG, display_name)
            return (None,) * len(getattr(self, "RETURN_TYPES", ()))

        if value is _MISSING:
            # 参数根本没传进来（节点结构被改动过，或上游未连接）。
            # 这不是"空输入"，交回核心处理，让它抛出有意义的错误。
            return core_func(self, *args, **kwargs)

        try:
            return core_func(self, *args, **kwargs)
        except Exception as exc:
            logger.warning(
                "%s %s：读取失败（%s），降级输出 None。",
                LOG_TAG, display_name, exc,
            )
            return (None,) * len(getattr(self, "RETURN_TYPES", ()))

    return _run


def _build_safe_node(base_cls, class_name: str, file_field: str, display_name: str):
    """基于核心节点类构建空输入安全版本。"""
    func_name = getattr(base_cls, "FUNCTION", None)
    if not func_name:
        return None
    core_func = getattr(base_cls, func_name, None)
    if core_func is None:
        return None

    # 真实字段名以核心类为准（核心 Load Video 的字段叫 "file"）
    real_field = _detect_file_field(base_cls, file_field)

    runner = _make_runner(core_func, real_field, display_name)
    runner.__name__ = func_name
    runner.__qualname__ = f"{class_name}.{func_name}"

    namespace = {
        "FUNCTION": func_name,
        "_file_field": real_field,
        "CATEGORY": CATEGORY,
        "DISPLAY_NAME": display_name,
        "DESCRIPTION": DESCRIPTION,
    }
    namespace[func_name] = runner
    return type(class_name, (_EmptySafeMixin, base_cls), namespace)


# ---------------------------------------------------------------------------
# VIDEO → IMAGE 抽帧
# ---------------------------------------------------------------------------
#
# 核心的 VIDEO 类型内部是一个"惰性视频对象"（Input.Video），
# 官方把 VIDEO 拆成 IMAGE / AUDIO 的节点是 `GetVideoComponents`，
# 它走的是：
#
#     components = video.get_components()
#     components.images     # torch.Tensor，形状 [帧数, H, W, C]，float32，0~1
#
# 本包沿用**完全相同**的取值路径，保证：
#   - 像素格式一致（8bit 视频是 uint8 解码后 /255，浮点视频直接读 float 平面）；
#   - 旋转角（rotation metadata）处理一致；
#   - HDR / 10bit 的处理一致。
#
# 但核心那条路径会**解出全部帧**。一个 10 秒 30fps 1080p 的视频就是
# 300 帧 × 1920×1080×3 × 4 字节 ≈ 7.4 GB，直接爆内存。
# 所以这里先用 `as_trimmed()` 把时间窗口裁短，只解出需要的部分。
#
# 裁剪方式：按帧率算出要取多少帧的时间长度，裁出一个**连续**窗口。
# 连续窗口对视频编码最友好（顺序解码），比逐帧随机 seek 快得多。
#
# 帧数上限的默认值 `_DEFAULT_MAX_FRAMES` 是个保守值：
# 16 帧 × 1920×1080×3×4 ≈ 380 MB，是绝大多数机器都扛得住的。


# 默认最多抽取多少帧。0 = 不限（用完整视频的帧数）。
# 16 帧在 1080p 下约占 380MB 内存，安全。
_DEFAULT_MAX_FRAMES = 16


def _extract_video_components(video, max_frames: int, skip_first_frames: int = 0,
                              select_every_nth: int = 1, force_rate: float = 0.0):
    """从 VIDEO 对象里抽出 IMAGE 张量 **和** AUDIO。

    返回 ``(images, audio)``：

    - ``images``：torch.Tensor，形状 ``[帧数, H, W, C]``，float32，取值 0~1。
      抽不出帧时是 ``None``。
    - ``audio``：``{"waveform": tensor[B, C, T], "sample_rate": int}``；
      视频没有音轨时是 ``None``。

    **音轨跟随抽帧窗口**：核心的 ``get_components()`` 一次解码就同时给出
    画面和声音，而且声音**在核心内部就已经按 trim 窗口裁过了**
    （见 ``get_components_internal``：

        audio_data = audio_data[..., :int(duration * audio_stream.sample_rate)]

    开头也有对应的跳过（``to_skip = offset_seconds * sample_rate``））。
    所以这里只要用**同一个** ``as_trimmed`` 窗口去解，画面和音轨就天然
    严格对齐——不需要单独再解一遍音频，也不需要手动裁剪。

    这与 VHS 的做法一致：VHS 也是
    ``lazy_get_audio(video, start_time, frame_load_cap * target_frame_time)``，
    即音频跟着加载帧数的窗口走。

    参数
    ----
    video:
        核心的 ``Input.Video`` 对象（``LoadVideo`` 的输出）。
    max_frames:
        最多抽多少帧。``0`` 表示不限。
    skip_first_frames:
        跳过开头的帧数。
    select_every_nth:
        隔几帧取一帧。``1`` = 全取。
    force_rate:
        强制帧率。``0`` = 保持原帧率。

    参数
    ----
    video:
        核心的 ``Input.Video`` 对象（``LoadVideo`` 的输出）。
    max_frames:
        最多抽多少帧。``0`` 表示不限。
    skip_first_frames:
        跳过开头的帧数。
    select_every_nth:
        隔几帧取一帧。``1`` = 全取。
    force_rate:
        强制帧率。``0`` = 保持原帧率。

    返回
    ----
    ``(images, audio)``。``images`` 是 torch.Tensor，形状 ``[帧数, H, W, C]``，
    float32，取值 0~1；抽不出帧时是 ``None``。
    ``audio`` 是 ``{"waveform": ..., "sample_rate": ...}``；无音轨时是 ``None``。

    处理顺序与 VHS 一致：

        跳过开头 → 重采样帧率 → 隔帧取 → 帧数封顶

    帧率重采样放在隔帧取**之前**，跟 VHS 的 ``fps=`` 滤镜位置一致
    （VHS 是先 fps 再 select_every_nth）。
    """
    if video is None:
        return (None, None)

    # 先看看视频有多少帧、帧率多少。
    # 这两个都是纯元数据查询，不解码画面，很便宜。
    #
    # total_frames 用 None 表示"探测不出来"，0 表示"确实一帧都没有"——
    # 两者要分开：探测失败时不能直接返回 None（那样有帧的视频也被误杀）。
    try:
        total_frames = int(video.get_frame_count())
    except Exception:
        total_frames = None
    try:
        fps = float(video.get_frame_rate())
    except Exception:
        fps = 0.0

    if total_frames is not None and total_frames <= 0:
        # 视频里一帧都没有（空文件 / 损坏）
        return (None, None)

    skip_first_frames = max(0, int(skip_first_frames or 0))
    select_every_nth = max(1, int(select_every_nth or 1))
    force_rate = float(force_rate or 0)

    # 跳过的帧已经超出视频长度 → 没有帧可取
    if total_frames is not None and skip_first_frames >= total_frames:
        return (None, None)

    # 跳过开头之后还剩多少帧
    if total_frames is None:
        available = None
    else:
        available = total_frames - skip_first_frames

    # 隔帧取之后能拿到多少帧
    if available is None:
        kept_by_nth = None
    else:
        kept_by_nth = (available + select_every_nth - 1) // select_every_nth

    # 最终要保留多少帧
    if kept_by_nth is None:
        want = max_frames if max_frames > 0 else 0
    elif max_frames <= 0:
        want = kept_by_nth
    else:
        want = min(max_frames, kept_by_nth)

    # 帧率重采样：**在时间轴上做**，而不是对已解码的帧做。
    #
    # 降帧率时，源帧里有一部分根本不需要解码。
    # 先算出目标帧率下要保留多少帧、以及这些帧对应源里的哪些位置，
    # 再据此裁解码窗口——解码器只解需要的部分。
    #
    # 目标帧率下的帧数：
    #   源剩余时长 = available / fps
    #   目标帧数   = 源剩余时长 × force_rate
    resampling = force_rate > 0 and fps > 0
    resampled_count = None
    if resampling and available is not None:
        duration = available / fps
        resampled_count = max(1, int(round(duration * force_rate)))

    # 最终保留多少帧（帧率重采样会改变帧数，所以要重新算）
    if resampled_count is not None:
        after_rate = resampled_count
        # 隔帧取之后
        after_nth = (after_rate + select_every_nth - 1) // select_every_nth
        if max_frames > 0:
            want = min(max_frames, after_nth)
        else:
            want = after_nth

    # 裁一个只包含这些帧的时间窗口，让核心的解码器只解这么多帧。
    #
    # as_trimmed(start_time, duration) 返回一个新的 Video 对象，
    # 它内部会按 duration 停止解码——这正是我们要的。
    #
    # 窗口长度按"要解码多少**源**帧"算。
    # 推导：最终保留的第 j 帧（j=0..want-1）在**重采样后**的序列里索引是 j*nth，
    # 它的时间 t = (j*nth)/force_rate 秒；源里同一时刻的帧索引是 t*fps。
    # 于是需要的源帧数 ≈ want × nth × fps / force_rate。
    # （没有重采样时 force_rate 视为 fps，公式退化成 want × nth。）
    #
    # 注意重采样时**故意多解一点**（+2 帧余量）：多解出来的帧在
    # _resample_frames_by_rate 里按目标帧数精确取帧，不会影响最终帧数。
    source = video
    if fps > 0 and want > 0:
        effective_rate = force_rate if resampling else fps
        if resampling:
            need = int(round(want * select_every_nth * (fps / effective_rate))) + 2
        else:
            need = want * select_every_nth
        need = max(1, need)

        if available is None or need < available:
            try:
                trimmed = video.as_trimmed(
                    skip_first_frames / fps,
                    need / fps,
                    strict_duration=False,
                )
                if trimmed is not None:
                    source = trimmed
                else:
                    # 裁剪失败（视频比声明的短）——退回到不裁剪
                    source = video
            except Exception:
                source = video

    # 走核心自己的解码路径取帧 **和** 音轨。
    #
    # 核心的 get_components() 一次解码就同时给出画面和声音，
    # 而且声音**在核心内部就已经按这个 trim 窗口裁过了**
    # （get_components_internal 里：
    #     audio_data = audio_data[..., :int(duration * sample_rate)]
    #  开头也有对应的 to_skip 偏移）。
    #
    # 所以音轨天然就跟随抽帧窗口，不需要单独再解一遍、也不用手动裁。
    try:
        components = source.get_components()
    except Exception:
        return (None, None)

    images = getattr(components, "images", None)
    audio = getattr(components, "audio", None)

    # 画面全空（视频损坏等）时，音轨仍然可能是好的，
    # 所以这里只把画面置 None，音轨照常返回。
    if images is None:
        return (None, audio)

    # 帧率重采样：按目标帧率从源帧里挑（时间上最接近的那些帧）
    if resampling:
        # 精确指定产出帧数：重采样后 want×nth 帧，隔帧取后正好 want 帧。
        # 这样"实际解出多少帧"不会影响最终帧数。
        images = _resample_frames_by_rate(
            images, fps, force_rate,
            target_count=want * select_every_nth,
        )

    # 隔帧取
    if select_every_nth > 1:
        try:
            images = images[::select_every_nth]
        except Exception:
            pass

    # 帧数封顶
    if want and images is not None and hasattr(images, "shape"):
        try:
            if images.shape[0] > want:
                images = images[:want]
        except Exception:
            pass

    # 核心的 frames_to_tensor() 在"一帧都没解出来"时返回的是
    # torch.zeros(0, 0, 0, 3)——一个**空张量**，不是 None。
    # 直接把它当 IMAGE 输出，下游会拿到一个 0 帧的怪东西。
    # 这里统一转成 None，语义上跟"抽不出帧"保持一致。
    try:
        if hasattr(images, "shape") and images.shape[0] == 0:
            images = None
    except Exception:
        pass

    return (images, audio)


def _extract_video_images(video, max_frames: int, skip_first_frames: int = 0,
                          select_every_nth: int = 1, force_rate: float = 0.0):
    """只抽画面（``_extract_video_components`` 的便捷包装）。

    返回 IMAGE 张量；抽不出帧时返回 ``None``。
    音频请直接用 ``_extract_video_components``。
    """
    images, _audio = _extract_video_components(
        video, max_frames,
        skip_first_frames=skip_first_frames,
        select_every_nth=select_every_nth,
        force_rate=force_rate,
    )
    return images


# ---------------------------------------------------------------------------
# 视频帧处理参数（VHS 风格）
# ---------------------------------------------------------------------------
#
# 核心的 LoadVideo 只有 ``file`` 一个输入，没有帧率 / 尺寸 / 抽帧的控制。
# 这里补一套 VideoHelperSuite (VHS) 风格的参数，语义与 VHS 对齐：
#
#   force_rate         强制帧率。0 = 保持原帧率。其它值 = 重新采样到这个帧率。
#   custom_width       目标宽度。0 = 保持原始宽度。
#   custom_height      目标高度。0 = 保持原始高度。
#   skip_first_frames  跳过开头的帧数。
#   select_every_nth   隔几帧取一帧。1 = 全取（默认）。
#
# 与 VHS 的两处差异（都是本包刻意做的）：
#
# 1. **不做 8 像素对齐**。VHS 的 target_size() 会把宽高对齐到 8 的倍数
#    （为了编码器兼容），本包不做——对齐会让用户填的 1920 变成 1920、
#    但填 1000 变成 1000 之类，容易让人困惑。
#
# 2. **宽高只填一个时保持宽高比**（与 VHS 一致）：
#    只填 custom_width → 高度按原比例算；只填 custom_height → 宽度按原比例算。
#    两个都填 → 强制拉伸到指定尺寸。
#
# 帧率重采样用的是"按时间戳就近取帧"的做法：
# 对每一帧算出它的时间戳 t，与目标帧序列 t_target 比较，
# 取时间上最接近的那一帧。这样帧率降低时不会丢内容，
# 帧率升高时则只能重复已有帧（源视频里根本没有那么多帧）。


def _target_size(width, height, custom_width, custom_height):
    """按 VHS 的语义算出目标尺寸。

    返回 ``(width, height)``；``(0, 0)`` 表示不用缩放。
    """
    custom_width = int(custom_width or 0)
    custom_height = int(custom_height or 0)

    if custom_width <= 0 and custom_height <= 0:
        return (0, 0)
    if width <= 0 or height <= 0:
        return (0, 0)

    if custom_height <= 0:
        # 只给了宽度 → 高度按原比例
        new_w = custom_width
        new_h = int(round(height * (custom_width / width)))
    elif custom_width <= 0:
        # 只给了高度 → 宽度按原比例
        new_h = custom_height
        new_w = int(round(width * (custom_height / height)))
    else:
        new_w = custom_width
        new_h = custom_height

    new_w = max(1, int(new_w))
    new_h = max(1, int(new_h))
    return (new_w, new_h)


def _resample_frames_by_rate(images, src_fps, target_fps, target_count=None):
    """按目标帧率重采样帧序列。

    做法：算出每一帧在**目标时间轴**上应该落在哪个位置，
    然后从源里挑时间上最接近的帧。

    - 降帧率（target < src）：丢帧，每 target_fps/src_fps 帧取一帧。
    - 升帧率（target > src）：重复帧，最多把每帧用满。

    ``target_count``：
        指定要产出多少帧。给定后，输出帧数就是它——**与实际解出了多少帧无关**，
        这样「解码窗口裁多了一点/少了一点」都不会影响最终帧数。
        不给则按"源帧数换算出的时长 × 目标帧率"算。
    """
    n = int(images.shape[0])
    if n <= 0:
        return images

    src_fps = float(src_fps or 0)
    target_fps = float(target_fps or 0)
    if src_fps <= 0 or target_fps <= 0:
        return images

    if target_count is not None:
        out_n = max(1, int(target_count))
    else:
        # 源视频的时长（秒）
        duration = n / src_fps
        # 目标帧数
        out_n = max(1, int(round(duration * target_fps)))

    # 逐个目标帧，找时间上最接近的源帧
    src_idx = []
    for i in range(out_n):
        t = i / target_fps
        j = int(round(t * src_fps))
        j = max(0, min(n - 1, j))
        src_idx.append(j)

    try:
        return images[src_idx]
    except Exception:
        # 老版本 torch 不支持列表索引，退回切片方式
        return images


def _scale_frames(images, width, height):
    """把帧序列缩放到指定尺寸。

    用 torch 的 interpolate（bicubic），与核心 ImageScale 用的
    ``common_upscale`` 走同一套 PyTorch 算子，结果一致。
    """
    if not width or not height:
        return images
    if images is None:
        return None

    try:
        import torch
    except Exception:
        return images

    try:
        n, h, w, c = images.shape
    except Exception:
        return images

    if h == height and w == width:
        return images

    try:
        # [N,H,W,C] → [N,C,H,W]
        x = images.permute(0, 3, 1, 2).contiguous()
        x = torch.nn.functional.interpolate(
            x, size=(height, width), mode="bicubic", align_corners=False
        )
        # → [N,H,W,C]
        return x.permute(0, 2, 3, 1).contiguous()
    except Exception as exc:
        logger.warning("%s 缩放帧失败（%s），保持原尺寸。", LOG_TAG, exc)
        return images


def _collect_frame_options(kwargs):
    """从节点输入里收集帧处理参数。

    返回一个 dict；没有任何参数生效时返回空 dict。

    注意：``force_rate`` / ``skip_first_frames`` / ``select_every_nth``
    **不在这里**——它们在抽帧阶段就处理掉了（见 ``_extract_video_components``），
    因为那样才能让解码器只解需要的帧，不用解完再丢。
    """
    opts = {}

    for key in ("custom_width", "custom_height"):
        value = kwargs.get(key, 0)
        try:
            value = int(value or 0)
        except Exception:
            value = 0
        if value > 0:
            opts[key] = value

    return opts


def _apply_frame_options(images, opts):
    """按参数对帧序列做缩放。

    ``opts`` 是一个 dict，键为 ``custom_width`` / ``custom_height``。

    帧率重采样（``force_rate``）不在这里做——它在抽帧阶段就处理完了
    （见 ``_extract_video_components``），因为那样才能只解码需要的帧。

    返回处理后的张量。
    """
    if images is None:
        return None

    custom_width = int(opts.get("custom_width") or 0)
    custom_height = int(opts.get("custom_height") or 0)

    tw, th = _target_size(
        images.shape[2], images.shape[1], custom_width, custom_height
    )
    if tw and th:
        images = _scale_frames(images, tw, th)

    return images


def _build_video_from_images(images, frame_rate, source_video):
    """用处理后的帧重建一个 VIDEO 对象。

    .. deprecated::
        本包最终采用的策略是"帧处理参数只作用于 IMAGE 输出"，
        VIDEO 保持原视频不变（保留音轨与画布预览）。
        这个函数保留下来以备将来需要，但**当前没有被调用**。

    原本的用途：让 VIDEO 输出与 IMAGE 输出**逐帧一致**。
    代价（也是当初没这么做的原因）：
    - 音轨会丢失（重建的视频只有画面）；
    - 画布预览需要重新编码，编码失败时预览就没有了。
    """
    if images is None:
        return None

    try:
        from comfy_api.latest import InputImpl, Types
    except Exception as exc:
        logger.warning("%s 无法导入 VideoFromComponents：%s", LOG_TAG, exc)
        return None

    try:
        fps = Fraction(float(frame_rate)) if frame_rate else Fraction(1)
    except Exception:
        fps = Fraction(1)

    # 尽量保留源视频的位深与色彩空间，避免 HDR 被当成 sRGB 处理
    bit_depth = 8
    color_space = "sRGB"
    chroma_subsampling = None
    if source_video is not None:
        for getter, setter in (
            ("get_bit_depth", "bit_depth"),
            ("get_color_space", "color_space"),
            ("get_chroma_subsampling", "chroma_subsampling"),
        ):
            try:
                value = getattr(source_video, getter, None)
                if callable(value):
                    value = value()
                if value is not None:
                    if setter == "bit_depth":
                        bit_depth = int(value)
                    elif setter == "color_space":
                        color_space = str(value)
                    else:
                        chroma_subsampling = value
            except Exception:
                pass

    try:
        return InputImpl.VideoFromComponents(
            Types.VideoComponents(images=images, frame_rate=fps),
            bit_depth=bit_depth,
            color_space=color_space,
            chroma_subsampling=chroma_subsampling,
        )
    except Exception as exc:
        logger.warning("%s 重建 VIDEO 失败：%s", LOG_TAG, exc)
        return None



# ---------------------------------------------------------------------------
# 核心：空输入安全混入类（用于 V3 节点）
# ---------------------------------------------------------------------------
#
# V3 节点与 V1 有几处关键差异，直接决定实现方式：
#
# 1. ``INPUT_TYPES`` 是 ``@final``，**不能覆盖**。
#    所以占位项只能通过覆写 ``define_schema()`` 来加：
#    在核心 schema 的基础上重建 inputs，把文件字段的 options
#    改成"空项 + 全部真实文件"，并把 default 设成空项。
#
# 2. ``FUNCTION`` 是 classmethod ``EXECUTE_NORMALIZED``，
#    内部调用 ``cls.execute(**kwargs)``（注意是 classmethod，不是实例方法）。
#    所以空输入判断要放在 ``execute`` 的覆写里。
#
# 3. ``validate_inputs`` 是 V3 的校验钩子（**小写**），
#    解析方式是 ``first_real_override(obj_class, "validate_inputs")``，
#    只认在基类之前定义的实现。
#
# 4. 覆写 ``execute`` 时必须仍然返回 ``io.NodeOutput``，
#    且输出个数要与 schema 一致（否则执行期会报错）。


# V3 基类（_ComfyNodeBaseInternal）上有一批**带缓存的 classproperty**：
#
#     _RETURN_TYPES = None
#     @final
#     @classproperty
#     def RETURN_TYPES(cls):
#         if cls._RETURN_TYPES is None:
#             cls.GET_SCHEMA()      # 首次访问时算出来，缓存在 cls._RETURN_TYPES
#         return cls._RETURN_TYPES
#
# 这些缓存值存在**类属性**上，赋值语句是 `cls._RETURN_TYPES = output`，
# 写进的是访问者的类。但读取时属性查找会**沿 MRO 向上找**，
# 所以：只要核心节点先被加载过（比如核心的 LoadVideo 先执行了一次
# GET_SCHEMA()），它的 `_RETURN_TYPES` 就已经被填成 `["VIDEO"]`；
# 我们继承核心类建的子类**没有自己的 `_RETURN_TYPES`**，
# 读取时就会拿到核心那份缓存 —— 于是我们的节点对外声称自己只有
# VIDEO 一个输出，尽管 define_schema 实际返回的是 VIDEO + IMAGE。
#
# 后果（用户实测到的报错）：
#     [ERROR] Failed to validate prompt for output 143:
#     [ERROR] * PreviewImage 143:
#     [ERROR]   - Exception when validating node: list index out of range
#
# 因为 ComfyUI 的校验代码按 slot 索引取上游节点的输出类型：
#     r = nodes.NODE_CLASS_MAPPINGS[o_class_type].RETURN_TYPES
#     received_type = r[val[1]]          # <-- 这里 IndexError
# 前端按 object_info 显示的输出数（正确，是 VIDEO+IMAGE）连到了 slot 1，
# 后端按缓存的 RETURN_TYPES（错误，只有 VIDEO）去取 slot 1 → 越界。
#
# 修法：在子类上把这些缓存属性**重置为 None**，让它按自己的
# define_schema 重新算一遍。重置的是子类自己的属性，
# 核心的缓存完全不受影响（不会污染核心自己的 LoadVideo）。
_V3_CACHE_ATTRS = (
    "SCHEMA",
    "_DESCRIPTION",
    "_CATEGORY",
    "_EXPERIMENTAL",
    "_DEPRECATED",
    "_DEV_ONLY",
    "_API_NODE",
    "_OUTPUT_NODE",
    "_HAS_INTERMEDIATE_OUTPUT",
    "_INPUT_IS_LIST",
    "_OUTPUT_IS_LIST",
    "_RETURN_TYPES",
    "_RETURN_NAMES",
    "_OUTPUT_TOOLTIPS",
    "_NOT_IDEMPOTENT",
    "_ACCEPT_ALL_INPUTS",
)


def _reset_v3_caches(namespace):
    """把 V3 的 classproperty 缓存属性在**子类自己身上**重置为 None。

    见上方 ``_V3_CACHE_ATTRS`` 的注释。
    """
    for attr in _V3_CACHE_ATTRS:
        namespace[attr] = None
    return namespace


def _build_safe_v3_node(base_cls, class_name: str, file_field: str,
                         display_name: str, extra_outputs=None):
    """基于 V3 核心节点构建空输入安全版本。

    做法：继承核心类，覆写 ``define_schema``（注入空占位项）、
    ``execute``（空输入降级为 None）和 ``validate_inputs``（空输入放行）。

    ``extra_outputs``：额外的输出项与附加输入，格式为
    ``(outputs, extra_inputs, executor)``。``outputs`` 是要追加到 schema
    末尾的 ``io.*.Output`` 列表；``extra_inputs`` 是附加的输入定义列表；
    ``executor`` 是 ``fn(core_result, kwargs) -> tuple``，
    负责从核心结果里算出附加输出的值。
    """
    try:
        from comfy_api.latest import io as comfy_io
    except Exception as exc:  # pragma: no cover - 取决于安装环境
        logger.warning("%s 无法导入 comfy_api.latest：%s", LOG_TAG, exc)
        return None

    real_field = _detect_v3_file_field(base_cls, file_field)

    base_schema = base_cls.define_schema
    base_execute = base_cls.execute
    base_validate = getattr(base_cls, "validate_inputs", None)

    extra_outputs = list(extra_outputs or [])
    extra_inputs = list(extra_outputs[1]) if extra_outputs else []
    extra_executor = extra_outputs[2] if extra_outputs else None
    # 附加输入的名字。核心的 execute() 不认识这些参数，
    # 调用核心时必须把它们剔掉（否则核心会因多余关键字参数报错）。
    extra_input_ids = [
        getattr(i, "id", None) for i in extra_inputs if getattr(i, "id", None)
    ]

    # --- 空输出 ---
    # V3 节点的输出个数由 schema 决定，这里按核心 schema 推导，
    # 保证"空输入"时输出的 None 个数与正常时一致。
    try:
        _probe = base_schema()
        out_count = len(_probe.outputs or [])
    except Exception:
        out_count = 1

    # 加上附加输出后的总输出个数
    total_out_count = out_count + len(extra_outputs[0]) if extra_outputs else out_count

    def define_schema(cls):
        """在核心 schema 基础上，给文件字段加一个排在首位的空占位项。

        同时改掉三项会显示给用户的元信息：
        - ``node_id``：改成新节点名，否则前端 / 工作流里会显示成核心的名字；
        - ``display_name``：同理；
        - ``category``：V3 节点的 ``CATEGORY`` 是个 ``@final`` 的 classproperty，
          值只能来自 ``schema.category``。不改的话节点会出现在核心的
          "video" / "audio" 分类下，前端补丁按分类名识别本包节点就会失效。
        """
        schema = base_schema()
        schema.node_id = class_name
        schema.display_name = display_name
        schema.category = CATEGORY
        schema.description = DESCRIPTION

        new_inputs = []
        patched = False
        for item in (schema.inputs or []):
            if getattr(item, "id", None) == real_field and not patched:
                patched = True
                item = _patch_v3_combo(item, real_field)
            new_inputs.append(item)

        # 附加输入放在文件输入之后、其余输入之前，
        # 这样它紧挨着"选文件"，用户一眼能看到。
        if extra_inputs:
            insert_at = 1 if new_inputs else 0
            new_inputs = (
                new_inputs[:insert_at]
                + list(extra_inputs)
                + new_inputs[insert_at:]
            )

        schema.inputs = new_inputs

        # 附加输出追加在核心输出之后
        if extra_outputs:
            schema.outputs = list(schema.outputs or []) + list(extra_outputs[0])

        return schema

    def execute(cls, **kwargs):
        """空输入时直接输出 None，不去碰文件系统。"""
        value = kwargs.get(real_field, _MISSING)
        if value is not _MISSING and _is_empty(value):
            logger.info("%s %s：未选择文件，输出 None。", LOG_TAG, display_name)
            return comfy_io.NodeOutput(*([None] * total_out_count))

        try:
            # 只把核心认识的参数传下去
            core_kwargs = {
                k: v for k, v in kwargs.items() if k not in extra_input_ids
            }
            result = base_execute(**core_kwargs)
        except Exception as exc:
            logger.warning(
                "%s %s：读取失败（%s），降级输出 None。",
                LOG_TAG, display_name, exc,
            )
            return comfy_io.NodeOutput(*([None] * total_out_count))

        if not extra_executor:
            return result

        # 从核心结果里取出附加输出
        try:
            extra_values = extra_executor(result, kwargs)
        except Exception as exc:
            logger.warning(
                "%s %s：附加输出计算失败（%s），该输出降级为 None。",
                LOG_TAG, display_name, exc,
            )
            extra_values = (None,) * len(extra_outputs[0])

        # 注意：必须把核心结果的 ui 一并带过去。
        # 核心的 LoadVideo 返回的是 NodeOutput(source, ui=preview_input_video(...))，
        # 那个 ui 里带着视频预览用的 SavedResult；丢掉它画布上就没有预览了。
        return comfy_io.NodeOutput(
            *(tuple(result.args) + tuple(extra_values)),
            ui=getattr(result, "ui", None),
        )

    def validate_inputs(cls, **kwargs):
        """文件为空时放行；非空时沿用核心的校验逻辑。"""
        value = kwargs.get(real_field, _MISSING)
        if value is not _MISSING and _is_empty(value):
            return True
        if base_validate is None:
            return True
        # 核心不认识我们新增的输入，必须剔掉再传下去
        core_kwargs = {
            k: v for k, v in kwargs.items() if k not in extra_input_ids
        }
        try:
            return base_validate(**core_kwargs)
        except NotImplementedError:
            return True
        except TypeError:
            # 核心的签名与预期不符（不同版本参数不同）——放行，
            # 真正的文件校验会在 execute 里以异常形式体现。
            return True

    # 核心的 fingerprint_inputs（V3）/ IS_CHANGED（V1）会对文件做哈希或取
    # 修改时间。空输入时文件根本不存在，直接调用会抛异常并在日志里刷警告。
    # 这里空输入时返回常量，让缓存判定走"未变化"分支。
    base_fingerprint = getattr(base_cls, "fingerprint_inputs", None)
    base_is_changed = getattr(base_cls, "IS_CHANGED", None)

    def fingerprint_inputs(cls, **kwargs):
        value = kwargs.get(real_field, _MISSING)
        if value is not _MISSING and _is_empty(value):
            return ""
        if base_fingerprint is None:
            return ""
        core_kwargs = {
            k: v for k, v in kwargs.items() if k not in extra_input_ids
        }
        try:
            fp = base_fingerprint(**core_kwargs)
        except Exception:
            return ""
        # 把附加输入的值也并进指纹。
        #
        # 核心的指纹只看文件修改时间，所以改了 max_frames 之后
        # 指纹不变，ComfyUI 会认为"没变化"而**跳过执行**——
        # 结果就是改了抽帧数却没反应。必须把附加参数也算进去。
        if extra_input_ids:
            try:
                extra = tuple(
                    kwargs.get(k) for k in extra_input_ids
                )
                return (fp, extra)
            except Exception:
                return fp
        return fp

    def is_changed(cls, *args, **kwargs):
        value = kwargs.get(real_field, _MISSING)
        if value is _MISSING and args:
            value = args[0]
        if value is not _MISSING and _is_empty(value):
            return ""
        if base_is_changed is None:
            return ""
        core_kwargs = {
            k: v for k, v in kwargs.items() if k not in extra_input_ids
        }
        try:
            return base_is_changed(*core_kwargs)
        except Exception:
            return ""

    namespace = {
        "define_schema": classmethod(define_schema),
        "execute": classmethod(execute),
        "validate_inputs": classmethod(validate_inputs),
        "_file_field": real_field,
        "CATEGORY": CATEGORY,
        "DISPLAY_NAME": display_name,
        "DESCRIPTION": DESCRIPTION,
    }
    # 只有核心确实实现了指纹函数时才覆写，避免引入无意义的钩子
    if base_fingerprint is not None:
        namespace["fingerprint_inputs"] = classmethod(fingerprint_inputs)
    if base_is_changed is not None:
        namespace["IS_CHANGED"] = classmethod(is_changed)

    # 关键：清掉继承自核心的 classproperty 缓存。
    # 否则核心 LoadVideo 的 _RETURN_TYPES（["VIDEO"]）会被我们继承，
    # 导致后端认为本节点只有一个输出，而前端按 VIDEO+IMAGE 连线
    # → 校验时 r[slot_index] 越界（"list index out of range"）。
    _reset_v3_caches(namespace)

    return type(class_name, (base_cls,), namespace)


def _patch_v3_combo(item, field_name: str):
    """给 V3 的 Combo 输入注入"排在首位的空占位项"。

    核心 V3 节点的选项列表里**没有** ``none`` 占位项。
    前端默认分支 ``WidgetSelectDefault`` 只有在选中值能于
    ``options.values`` 里匹配到时才显示该项的 label，
    匹配不到就显示原值（甚至回退到第一个文件）。
    所以这里把占位项插到第一位并设为 default，
    前端补丁再把选中值压成 ``"none"``，两边配合才能真正"默认为空"。

    注意这里是**原地修改**而不是重建一个 ``Combo.Input``：
    重建需要按构造函数的参数名逐个传参，而不同版本的 ComfyUI
    给 ``Combo.Input.__init__`` 加的参数并不一样（新版多了
    ``raw_link`` / ``advanced`` 等），照抄某一个版本的签名换到
    另一个版本上就会因为多传/少传参数直接抛 TypeError，
    进而让整个节点注册失败。原地改 ``options`` 和 ``default``
    两个属性则与版本无关。
    """
    options = list(getattr(item, "options", None) or [])
    if not options:
        return item

    token = None
    for o in options:
        if isinstance(o, str) and o.strip().lower() in _EMPTY_TOKENS:
            token = o
            break
    if token is None:
        token = _PLACEHOLDER

    filtered = [
        o for o in options
        if not (isinstance(o, str) and o.strip().lower() in _EMPTY_TOKENS)
    ]

    # 先浅拷贝再改。
    #
    # 核心的 define_schema() 有可能返回**共享/缓存**的 schema 对象。
    # 直接原地改它，会连带把核心自己的 LoadAudio 的 audio_upload 标记
    # 也摘掉 —— 那样核心节点就丢掉上传按钮了（属于污染核心）。
    #
    # 浅拷贝（copy.copy）保留对象上的**全部**属性，包括不同版本
    # Combo.Input 新增的那些（raw_link / advanced 等），
    # 既不用照抄构造函数的参数名（版本一换就崩），
    # 又不会碰到核心共享的对象。
    try:
        item = copy.copy(item)
    except Exception:
        # 拷贝失败就退回原地修改：风险是可能污染核心，但至少节点能注册。
        logger.warning(
            "%s 拷贝 Combo.Input 失败，改为原地修改（可能影响核心节点）：%s",
            LOG_TAG, _AUDIO_UPLOAD_MARKER,
        )

    item.options = [token] + filtered
    item.default = token
    _strip_audio_upload_marker(item)
    return item


def _strip_audio_upload_marker(item):
    """摘掉 Combo 输入上的 ``audio_upload`` 标记。

    为什么必须摘掉（而不能摘掉所有 ``*_upload``）
    ------------------------------------------------
    核心前端有两个音频扩展，各管一头：

    .. code-block:: javascript

        // Comfy.AudioWidget —— 按**节点名**硬编码注入播放器
        if (['LoadAudio','SaveAudio','PreviewAudio','SaveAudioMP3',
             'SaveAudioOpus','SaveAudioAdvanced'].includes(comfyClass))
            input.required.audioUI = ['AUDIO_UI', {}]

        // Comfy.UploadAudio —— 按**输入标记**注入上传按钮
        if (input.required.audio[1].audio_upload === true)
            input.required.upload = ['AUDIOUPLOAD', {}]

    两个条件**各管一头**，改名后的节点就掉进了夹缝：

    - 节点名 ``LoadAudioHervinai`` 不在白名单里 → 核心不给它注入
      ``audioUI`` 输入 → 画布上**没有** ``audioUI`` 控件；
    - 但后端继承了核心 ``LoadAudio`` 的 ``audio_upload`` 标记 →
      核心给它注入了 ``upload`` 输入 → 画布上**有** ``AUDIOUPLOAD`` 控件。

    而 ``AUDIOUPLOAD`` 控件的构造函数**第一件事**就是找那个
    不存在的 ``audioUI`` 控件：

    .. code-block:: javascript

        AUDIOUPLOAD(node) {
          const audioUI = node.widgets?.find(w => w.name === 'audioUI');  // undefined
          const update = () => updateUIWidget(audioUI, url);  // audioUI.element → 崩
          update();   // 立刻执行
        }

    ``updateUIWidget`` 第一行就是 ``widget.element.src = url``，
    于是抛
    ``TypeError: Cannot read properties of undefined (reading 'element')``，
    节点**直接加载不上画布**（搜索框里找得到，拖上去就报错）。

    摘掉标记后核心不会再注入 ``AUDIOUPLOAD``，播放器和上传按钮
    改由前端补丁自己建（``web/hervinai_none.js``），不依赖核心。

    **只摘 ``audio_upload``。**
    ``image_upload`` / ``video_upload`` 必须留着——核心的上传按钮
    （``useImageUploadWidget()``）是靠这些标记才会创建的，
    一起摘掉会让图片/视频节点退化成只有下拉框、没有上传按钮。

    这一点与社区节点包 ``WhatDreamsCost-ComfyUI`` 的做法一致：
    它的前端 ``load_audio_ui.js`` 完全没有处理 ``AUDIOUPLOAD``，
    播放器和上传全是自己 ``addDOMWidget`` / ``addWidget`` 建的。
    （本包不依赖那个项目，只是做法上得到了启发。）
    """
    marker = _AUDIO_UPLOAD_MARKER

    # 0) **最关键的一处**：V3 的 ``Combo.Input`` 并不把上传标记存成属性，
    #    而是在 ``as_dict()`` 里**动态生成**：
    #
    #        def as_dict(self):
    #            return super().as_dict() | prune_dict({
    #                ...
    #                **({self.upload.value: True} if self.upload is not None else {}),
    #            })
    #
    #    所以对象上压根没有 ``audio_upload`` 这个属性，
    #    前面那些 "setattr(item, marker, False)" 对 V3 完全无效。
    #    真正的开关是 ``self.upload``——把它置空，标记就不会生成。
    #
    #    ``self.upload`` 是 ``UploadType`` 枚举（str 的子类），
    #    比对时要用它的 ``.value``。
    try:
        upload = getattr(item, "upload", None)
        if upload is not None:
            value = getattr(upload, "value", upload)
            if isinstance(value, str) and value == marker:
                item.upload = None
    except Exception:
        pass

    # 1) 顶层属性：部分版本会把上传标记直接存成属性
    #    （服务端生成的 V1 兼容视图会读这个属性）。置 False 即可让标记消失。
    try:
        if hasattr(item, marker):
            setattr(item, marker, False)
    except Exception:
        pass

    # 2) 直接删掉属性（有些版本只在 __dict__ 里存一份）
    try:
        if hasattr(item, "__dict__") and marker in item.__dict__:
            item.__dict__[marker] = False
    except Exception:
        pass

    # 3) extra 字典（部分版本把额外选项放这里）
    try:
        extra = getattr(item, "extra", None)
        if isinstance(extra, dict):
            extra.pop(marker, None)
    except Exception:
        pass

    # 4) options 字典。
    #    注意：V1 路径下 item.options 是**字符串列表**（选项本身），
    #    V3 路径下是 Combo 的选项容器；两种都不该有 audio_upload，
    #    只有当它恰好是 dict 时才需要摘键。
    try:
        options = getattr(item, "options", None)
        if isinstance(options, dict):
            options.pop(marker, None)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 回退实现（仅在核心类缺失时启用）
# ---------------------------------------------------------------------------

class _FallbackLoadImage:
    """核心 LoadImage 不可用时的最小回退实现。"""

    FUNCTION = "load_image"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    CATEGORY = CATEGORY

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ([_PLACEHOLDER] + _list_input_files(),
                                        {"image_upload": True})}}

    def load_image(self, image, upload=None):
        import numpy as np
        import torch
        from PIL import Image, ImageOps

        input_dir = folder_paths.get_input_directory()
        full_path = os.path.join(input_dir, image)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"文件不存在: {full_path}")

        img = Image.open(full_path)
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        arr = np.array(img).astype(np.float32) / 255.0
        mask = torch.zeros((arr.shape[0], arr.shape[1], 1), dtype=torch.float32)
        return (arr, mask)


class _FallbackLoadAudio:
    """核心 LoadAudio 不可用时的最小回退实现。"""

    FUNCTION = "load_audio"
    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    CATEGORY = CATEGORY

    @classmethod
    def INPUT_TYPES(cls):
        # 不带 audio_upload 标记：带了核心就会注入 AUDIOUPLOAD 控件，
        # 那个控件需要一个我们提供不了的 audioUI 控件，构造函数会崩。
        # 详见 _strip_audio_upload_marker。
        return {"required": {"audio": ([_PLACEHOLDER] + _list_input_files(),
                                        {})}}

    def load_audio(self, audio, upload=None):
        import av
        import numpy as np
        import torch

        input_dir = folder_paths.get_input_directory()
        full_path = os.path.join(input_dir, audio)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"文件不存在: {full_path}")

        container = av.open(full_path)
        try:
            stream = container.streams.audio[0]
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="stereo", rate=stream.rate
            )
            chunks = []
            for frame in container.decode(stream):
                for out_frame in resampler.resample(frame):
                    chunks.append(out_frame.to_ndarray().reshape(-1))
            if not chunks:
                raise ValueError("音频中没有可解码的数据")
            waveform = torch.from_numpy(
                np.concatenate(chunks).astype("float32")
            ) / 32768.0
            rate = stream.rate
        finally:
            container.close()

        return ({"waveform": waveform.unsqueeze(0), "sample_rate": rate},)


class _FallbackLoadVideo:
    """核心 LoadVideo 不可用时的最小回退实现。"""

    FUNCTION = "load_video"
    RETURN_TYPES = ("VIDEO", "AUDIO")
    RETURN_NAMES = ("video", "audio")
    CATEGORY = CATEGORY

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"file": ([_PLACEHOLDER] + _list_input_files(),
                                       {"video_upload": True})}}

    def load_video(self, file, **kwargs):
        import av
        import numpy as np
        import torch

        input_dir = folder_paths.get_input_directory()
        full_path = os.path.join(input_dir, file)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"文件不存在: {full_path}")

        container = av.open(full_path)
        try:
            stream = container.streams.video[0]
            frames = []
            for frame in container.decode(stream):
                frames.append(frame.to_ndarray(format="rgb24"))
            if not frames:
                raise ValueError("视频中没有可解码的帧")
            # 统一尺寸（取第一帧尺寸），堆叠成 [H, W, C, N]
            h, w, c = frames[0].shape
            frames = [
                f if f.shape[:2] == (h, w) else np.resize(f, (h, w, c))
                for f in frames
            ]
            stacked = np.stack(frames, axis=-1)
            tensor = torch.from_numpy(stacked.astype("float32") / 255.0)
        finally:
            container.close()

        return ({"video": tensor, "fps": 8.0}, None)


# ---------------------------------------------------------------------------
# 组装节点
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def _register(class_name: str, node_cls, display_name: str):
    if node_cls is None:
        logger.warning("%s 节点 %s 构建失败，已跳过。", LOG_TAG, class_name)
        return
    NODE_CLASS_MAPPINGS[class_name] = node_cls
    NODE_DISPLAY_NAME_MAPPINGS[class_name] = display_name


def _build(class_name: str, base_cls, file_field: str, display_name: str,
           extra_outputs=None):
    """按核心节点的风格（V1 / V3）选择合适的构建方式。"""
    if _is_v3_node(base_cls):
        return _build_safe_v3_node(
            base_cls, class_name, file_field, display_name,
            extra_outputs=extra_outputs,
        )
    return _build_safe_node(base_cls, class_name, file_field, display_name)


# --- Load Image ------------------------------------------------------------
_base_image = _core_class("LoadImage")
if _base_image is None:
    _base_image = _FallbackLoadImage
    logger.warning("%s 未找到核心 LoadImage，使用内置回退实现。", LOG_TAG)

_safe_image = _build("LoadImageHervinai", _base_image, "image",
                     "Load Image Hervinai")
_register(
    "LoadImageHervinai",
    _safe_image,
    "Load Image Hervinai 空输入安全图像",
)

# --- Load Audio ------------------------------------------------------------
_base_audio = _core_class("LoadAudio")
if _base_audio is None:
    _base_audio = _FallbackLoadAudio
    logger.warning("%s 未找到核心 LoadAudio，使用内置回退实现。", LOG_TAG)

_safe_audio = _build("LoadAudioHervinai", _base_audio, "audio",
                     "Load Audio Hervinai")
_register(
    "LoadAudioHervinai",
    _safe_audio,
    "Load Audio Hervinai 空输入安全音频",
)

# --- Load Video ------------------------------------------------------------
_base_video = _core_class("LoadVideo", "LoadVideoOutput")
if _base_video is None:
    _base_video = _FallbackLoadVideo
    logger.warning("%s 未找到核心 LoadVideo，使用内置回退实现。", LOG_TAG)


def _make_video_image_outputs():
    """给 LoadVideoHervinai 组装额外的 IMAGE + AUDIO 输出。

    返回 ``(outputs, extra_inputs, executor)`` 三元组，
    格式与 ``_build_safe_v3_node`` 的 ``extra_outputs`` 参数一致。

    - ``outputs``：追加到 schema 末尾的输出定义
      （``io.Image.Output`` + ``io.Audio.Output``）；
    - ``extra_inputs``：额外的输入定义（抽帧参数那一组）；
    - ``executor``：``fn(core_result, kwargs) -> (images, audio)``，
      从核心结果里算出 IMAGE 和 AUDIO。

    如果核心的 ``io`` 模块不可用（极老版本），返回 ``None``，
    此时节点退化成只有 VIDEO 输出，功能不受影响。
    """
    try:
        from comfy_api.latest import io as comfy_io
    except Exception:
        return None

    # 实际启用了哪些附加输出（IMAGE / AUDIO 各自独立降级）。
    # 顺序必须跟 outputs 一致——``_executor`` 按这个顺序返回值。
    _enabled = []

    def _executor(core_result, kwargs):
        """从核心的 VIDEO 输出里抽出 IMAGE 和 AUDIO。

        返回 ``(images, audio)``。

        注意：**VIDEO 输出保持原视频不变**——这样画布预览还在。
        帧处理参数作用于 IMAGE 和 AUDIO 这一路。
        """
        try:
            video = core_result.args[0]
        except Exception:
            video = None

        if video is None:
            return (None, None)

        max_frames = kwargs.get("max_frames", _DEFAULT_MAX_FRAMES)
        try:
            max_frames = int(max_frames)
        except Exception:
            max_frames = _DEFAULT_MAX_FRAMES

        skip_first_frames = kwargs.get("skip_first_frames", 0)
        try:
            skip_first_frames = int(skip_first_frames or 0)
        except Exception:
            skip_first_frames = 0

        select_every_nth = kwargs.get("select_every_nth", 1)
        try:
            select_every_nth = int(select_every_nth or 1)
        except Exception:
            select_every_nth = 1

        force_rate = kwargs.get("force_rate", 0)
        try:
            force_rate = float(force_rate or 0)
        except Exception:
            force_rate = 0.0

        # 抽帧 + 取音轨（跳过开头 + 帧率重采样 + 隔帧取都在这里做，
        # 这样解码器只解需要的帧，不用解完全部再丢）
        #
        # 音轨由核心的 get_components() 在**同一个窗口**里一起解出来，
        # 所以它天然跟随抽帧窗口（详见 _extract_video_components 的注释）。
        images, audio = _extract_video_components(
            video,
            max_frames,
            skip_first_frames=skip_first_frames,
            select_every_nth=select_every_nth,
            force_rate=force_rate,
        )

        # 缩放（帧率已在抽帧阶段处理完）
        opts = _collect_frame_options(kwargs)
        if opts:
            images = _apply_frame_options(images, opts)

        # 按实际启用的输出顺序裁剪。
        #
        # IMAGE 和 AUDIO 是各自独立降级的（核心 io 模块可能只有其中一个），
        # 所以返回值必须跟 outputs 的顺序严格对应——
        # 否则前端 slot 0 拿到的是 AUDIO，slot 1 拿到的是 IMAGE，
        # 类型对不上，校验时就会报"list index out of range"。
        _all = {"images": images, "audio": audio}
        return tuple(_all[name] for name in _enabled)

    # 构造输出 / 输入定义。
    #
    # 核心的 io 模块在不同版本间差异不小（比如极老版本没有 io.Image /
    # io.Int / io.Audio），一旦这里抛异常就会**中断整个包的加载**
    # ——三个节点全部起不来。所以任何异常都降级，宁可少一路输出，
    # 也不能让整个包挂掉。
    #
    # IMAGE 和 AUDIO **各自独立**降级：
    #   - io.Image 缺失 → 只剩 AUDIO 输出
    #   - io.Audio 缺失 → 只剩 IMAGE 输出（回到加 AUDIO 之前的行为）
    #   - 两个都缺     → 只剩核心的 VIDEO 输出

    outputs = []
    try:
        outputs.append(comfy_io.Image.Output(
            display_name="images",
            tooltip="视频抽出的帧（IMAGE）。抽帧上限由 max_frames 控制。",
        ))
        _enabled.append("images")
    except Exception as exc:
        logger.warning("%s 核心 io 模块没有 Image 输出，跳过 images 输出：%s",
                       LOG_TAG, exc)

    try:
        outputs.append(comfy_io.Audio.Output(
            display_name="audio",
            tooltip="视频的音轨（AUDIO），跟随抽帧窗口截取。"
                    "视频没有音轨时输出 None。",
        ))
        _enabled.append("audio")
    except Exception as exc:
        logger.warning("%s 核心 io 模块没有 Audio 输出，跳过 audio 输出：%s",
                       LOG_TAG, exc)

    try:
        extra_inputs = [
            comfy_io.Int.Input(
                "max_frames",
                default=_DEFAULT_MAX_FRAMES,
                min=0,
                max=99999,
                optional=True,
                tooltip="最多抽取多少帧。0 = 不限（会解出全部帧，长视频可能占很多内存）。",
            ),
            comfy_io.Float.Input(
                "force_rate",
                default=0.0,
                min=0.0,
                max=240.0,
                step=0.1,
                optional=True,
                tooltip="强制帧率。0 = 保持原帧率。其它值 = 把帧序列重采样到这个帧率。",
            ),
            comfy_io.Int.Input(
                "custom_width",
                default=0,
                min=0,
                max=16384,
                optional=True,
                tooltip="目标宽度。0 = 保持原始宽度。只填宽度时，高度按原比例自动算。",
            ),
            comfy_io.Int.Input(
                "custom_height",
                default=0,
                min=0,
                max=16384,
                optional=True,
                tooltip="目标高度。0 = 保持原始高度。只填高度时，宽度按原比例自动算。",
            ),
            comfy_io.Int.Input(
                "skip_first_frames",
                default=0,
                min=0,
                max=1000000,
                optional=True,
                tooltip="跳过开头的帧数。",
            ),
            comfy_io.Int.Input(
                "select_every_nth",
                default=1,
                min=1,
                max=1000000,
                optional=True,
                tooltip="隔几帧取一帧。1 = 全取（默认）。填 2 就是每两帧取一帧。",
            ),
        ]
    except Exception as exc:
        logger.warning(
            "%s 构造 IMAGE 附加输出失败，视频节点将只有 VIDEO 输出：%s",
            LOG_TAG, exc,
        )
        return None

    return (outputs, extra_inputs, _executor)


_safe_video = None
try:
    _safe_video = _build("LoadVideoHervinai", _base_video, "file",
                          "Load Video Hervinai",
                          extra_outputs=_make_video_image_outputs())
except Exception as exc:
    # 附加的 IMAGE 输出只是"锦上添花"，它出问题不该影响视频节点本身。
    # 这里退回到不带附加输出的构建方式。
    logger.warning(
        "%s 构建 LoadVideoHervinai 的 IMAGE 附加输出失败，"
        "退回到只有 VIDEO 输出：%s", LOG_TAG, exc,
    )
    try:
        _safe_video = _build("LoadVideoHervinai", _base_video, "file",
                              "Load Video Hervinai")
    except Exception as exc2:
        logger.warning("%s 构建 LoadVideoHervinai 失败：%s", LOG_TAG, exc2)

_register(
    "LoadVideoHervinai",
    _safe_video,
    "Load Video Hervinai 空输入安全视频",
)


# ---------------------------------------------------------------------------
# 说明：不覆盖核心节点
# ---------------------------------------------------------------------------
# 本包**不注册** LoadImage / LoadAudio / LoadVideo 这三个核心同名键，
# 核心节点保持原样、行为完全不变。删除本目录即可完全卸载。
#
# 代价：核心前端为视频 / 音频节点提供的预览播放器，是按节点类型名
# 匹配的扩展（只认 LoadVideo / LoadAudio），新名字匹配不上。
# 这一块由 web/hervinai_none.js 里的前端扩展补齐（见该文件注释）。
#
# 输出实际使用的字段名与节点风格，便于排查
for _key, _cls in NODE_CLASS_MAPPINGS.items():
    logger.info(
        "%s %s 使用文件字段：%s（%s）",
        LOG_TAG, _key, _cls._file_field,
        "V3 schema" if _is_v3_node(_cls) else "V1",
    )
