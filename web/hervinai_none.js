/**
 * comfyui-hervinai-none —— 前端补丁
 *
 * 解决三个问题
 * ============
 *
 * 1) 默认值必须为空
 * --------------------
 *    视频 / 音频节点的"文件"控件由 ComfyUI 前端自己渲染
 *    （预览播放器、上传框等）。
 *
 *    前端的 `WidgetSelect` 组件有三个分支：资源选择器、控制型控件、
 *    以及默认的 `WidgetSelectDefault`。当前走的是默认分支，它的显示
 *    逻辑是：
 *
 *      - 选中值能在 `options.values` 里匹配到 → 显示该项的 label；
 *      - 匹配不到 → 显示原值（某些分支还会回退显示第一个文件）。
 *
 *    所以"默认为空"要同时满足两个条件：
 *      - `"none"` 必须**真的在选项列表里**，且排在第一位；
 *      - 选中值设为 `"none"`。
 *
 *    只把值设成 `"none"` 而不把它加进选项列表是不行的——前端会认为
 *    这是一个"文件已丢失"的选项，反而回退显示第一个真实文件。
 *
 *    另外这个控件在节点创建时会**异步**拉取 input 目录的文件列表，
 *    然后自行把选中值设成列表里的第一个文件，把我们的设置覆盖掉。
 *    因此需要"用户交互探测 + 轮询回正"：
 *    挂钩控件的 setValue / callback，一旦发现写进来的是非空值，
 *    就认定是用户主动选的，立刻结束保护窗口；
 *    保护窗口内若发现值非空而用户没操作过，就拉回 "none"。
 *
 *    不能用 defineProperty 拦截 value 写入：前端 ComboWidget.setValue
 *    是"先写值、再回调"，拦截会在用户刚选好文件的瞬间把写入吞掉，
 *    表现为"选了文件但没反应"。详见 hookWidgetInteraction 的注释。
 *
 *    LoadImage 走通用 `image_upload` widget，行为略有不同，
 *    但同样按 "none" 处理即可。
 *
 * 2) 后端必须能认出这个空值
 * ----------------------
 *    空输入最终要传回后端。核心 V3 节点（LoadVideo / LoadAudio）的
 *    选项列表里本来就没有占位项，本包在 `define_schema` 里补了一个
 *    排在首位的空项；后端 `_is_empty()` 会把 "none" 判为空输入，
 *    输出 None 而不报错。
 *
 * 3) 音频预览 + 上传按钮
 * ----------------------
 *    核心前端有两个音频扩展（打包在 `core-*.js` 里）：
 *
 *      Comfy.AudioWidget —— 按**节点名硬编码**注入播放器：
 *          if (['LoadAudio','SaveAudio','PreviewAudio','SaveAudioMP3',
 *               'SaveAudioOpus','SaveAudioAdvanced'].includes(comfyClass))
 *              input.required.audioUI = ['AUDIO_UI', {}]
 *
 *      Comfy.UploadAudio —— 按**输入标记**注入上传按钮：
 *          if (input.required.audio[1].audio_upload === true)
 *              input.required.upload = ['AUDIOUPLOAD', {}]
 *
 *    两个条件是**各管一头**的，于是改名后的节点会掉进夹缝：
 *
 *      - 节点名不匹配 → 核心不给它注入 `audioUI` 输入，
 *        于是**没有** `audioUI` 控件；
 *      - 但后端继承了核心 `LoadAudio` 的 `audio_upload` 标记 →
 *        核心给它注入了 `upload` 输入，于是**有** `AUDIOUPLOAD` 控件。
 *
 *    而 `AUDIOUPLOAD` 控件的构造函数第一件事就是找 `audioUI` 控件：
 *
 *      AUDIOUPLOAD(node) {
 *        const audioUI = node.widgets?.find(w => w.name === 'audioUI');  // undefined
 *        const update = () => updateUIWidget(audioUI, url);  // audioUI.element → 崩
 *        update();   // 立刻执行
 *      }
 *
 *    `updateUIWidget` 第一行就是 `widget.element.src = url`，
 *    于是抛 `TypeError: Cannot read properties of undefined (reading 'element')`，
 *    节点直接加载不上画布（搜索框里找得到，拖上去就报错）。
 *
 *    修法：**不让核心注入 `AUDIOUPLOAD`**（见 disableCoreAudioUpload），
 *    播放器和上传按钮改由本补丁在 `onNodeCreated` 里直接建。
 *
 *    这里**不**往节点定义里注入 `["AUDIO_UI", {}]`：
 *    前端渲染节点时，先跑 `addInputSocket` 给每个输入建插槽，
 *    再跑 `addInputWidget` 找对应控件。只有"已注册的控件类型"
 *    才会被 `addInputWidget` 认领；而 `addInputSocket` 对
 *    **未知类型**照样会建一个插槽（只是没有控件绑定），
 *    容易留下悬空插槽。本补丁全程不碰节点定义，天然没有这个问题。
 *
 *    播放器的控件名必须叫 `audioUI`（见 addAudioControls 里的注释）——
 *    核心执行完之后也要按这个名字找它来刷新播放器。
 *
 *    视频播放器不受影响：它是按**输出类型 VIDEO** 渲染的，
 *    与节点名无关，改名后依然正常。
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// 空状态下显示的提示文案（同时也是选中值）。
// 默认分支里 options.placeholder 只用作搜索框提示，
// 真正显示出来的是选中值对应的 label，所以选中值也用 "none"。
const PLACEHOLDER = "none";

// 后端识别"空输入"时用的占位字面值（与后端 _PLACEHOLDER 保持一致）。
// 选中值用它，而不是 null：null 在默认分支里只显示成空白，
// 看上去像"控件坏了"，而 "none" 能明确表达"这里故意留空"。
const EMPTY_TOKEN = "none";

// 本包涉及的文件字段名
const FILE_FIELDS = ["file", "audio", "image"];

// 需要补音频播放器的节点名
const AUDIO_NODE_CLASS = "LoadAudioHervinai";

// 本包注册的全部节点名（用于识别，category 之外的兜底）
const OWN_NODE_CLASSES = [
    "LoadImageHervinai",
    "LoadAudioHervinai",
    "LoadVideoHervinai",
];

// 保护窗口：节点创建后多久内强制保持空输入
const GUARD_WINDOW_MS = 5000;

// 轮询间隔
const POLL_INTERVAL_MS = 100;

/**
 * 是否属于"空值"。
 *
 * null / undefined 是真正的空（前端选中态为空的表示）；
 * 空串与 "none" 是后端占位字面值，工作流里可能存着。
 */
function isEmptyToken(value) {
    if (value === null || value === undefined) return true;
    if (typeof value !== "string") return false;
    const t = value.trim().toLowerCase();
    return t === "" || t === EMPTY_TOKEN;
}

function findFileWidgets(node) {
    const widgets = node?.widgets;
    if (!Array.isArray(widgets)) return [];
    return widgets.filter(
        (w) => w && FILE_FIELDS.includes(w.name) && w.options
    );
}

/**
 * 设置空状态提示文案。
 *
 * 前端 `WidgetSelect` 有三个分支，走哪个取决于控件类型：
 *   - 资源选择器分支（仅当 assets 商店启用时）
 *   - control widget 分支
 *   - 默认分支 `WidgetSelectDefault`（当前默认）
 *
 * 默认分支里 `options.placeholder` 只用作搜索框的提示文案，
 * **不作为空态显示文本**。空态显示的是：
 *   - 选中值能在 options 里匹配到 → 显示该项的 label
 *   - 选中值为 null → 显示空字符串
 *
 * 所以要显示 "none"，选中值必须是字符串 "none"，
 * 而且 "none" 必须真的在 options 列表里（后端会把它插到第一位）。
 */
function setPlaceholder(widget) {
    const opts = widget.options;
    if (!opts) return;
    if (opts.placeholder !== PLACEHOLDER) {
        opts.placeholder = PLACEHOLDER;
    }
}

/**
 * 给控件挂上"用户交互"探测。
 *
 * 为什么只是探测、而不是拦截写入：
 *
 * 前端 `ComboWidget.setValue` 的执行顺序是
 *     this.value = a;              // 先写值
 *     this.callback?.(a, ...);     // 再回调
 * 如果用 defineProperty 拦截 value 赋值，用户在保护窗口内
 * 刚点完下拉框选好文件，写入就会当场被吞掉——等 callback 里
 * 再补救已经晚了，用户看到的就是"选了文件但没反应"。
 *
 * 所以这里不拦写入，只做两件事：
 *   1. 记录"用户已经主动操作过"，之后不再回正；
 *   2. 保护窗口内若发现值变成了非空，且用户没操作过，就拉回 "none"。
 */
function hookWidgetInteraction(node, widget, state) {
    if (widget.__hervinaiHooked) return;
    widget.__hervinaiHooked = true;

    // 画布上点选下拉项走 setValue：这是最明确的用户意图信号，
    // 在它写值之前就标记，避免被保护窗口误伤。
    if (typeof widget.setValue === "function") {
        const originalSetValue = widget.setValue.bind(widget);
        widget.setValue = function (value, ...rest) {
            if (!isEmptyToken(value)) {
                markUserInteraction(node);
            }
            return originalSetValue(value, ...rest);
        };
    }

    // 新前端（Vue 节点）改值走 callback，也在这里兜底。
    // 只有"非空值"才算用户操作：我们自己回正时写的 "none" 不算。
    const originalCallback = widget.callback;
    widget.callback = function (value, ...rest) {
        if (!isEmptyToken(value)) {
            markUserInteraction(node);
        }
        return originalCallback?.apply(this, [value, ...rest]);
    };
}

/**
 * 确保选项列表里有 "none" 占位项，并把它排在第一位。
 *
 * 为什么必须有这个占位项：
 * 前端默认分支 `WidgetSelectDefault` 显示选中项 label 的条件是
 * "选中值能在 options 里匹配到"。匹配不到时它会显示原值，
 * 而某些控件（例如资源选择器分支）匹配不到还会**回退显示第一个文件**。
 *
 * 把 "none" 放进 options 并置于首位，就同时满足三件事：
 *   - 默认选中 "none" 而不是第一个真实文件；
 *   - 界面上显示的文字就是 "none"（取该项的 label）；
 *   - "none" 是合法选项，不会被当成"文件已丢失"。
 *
 * 后端也会做同样的事（把 none 插到 options 首位并设为 default），
 * 这里再补一次是为了覆盖"后端是旧版本 / 前端先于后端拿到数据"的情况，
 * 并且能顺带修掉核心把占位项排在中间的情况。
 */
function ensureNoneOption(widget) {
    const opts = widget.options;
    if (!opts) return;

    let values = opts.values;
    if (!Array.isArray(values)) {
        if (typeof values === "function") {
            try {
                values = values();
            } catch (err) {
                values = null;
            }
        }
        if (!Array.isArray(values)) return;
    }

    const filtered = values.filter(
        (v) => !(typeof v === "string" && v.trim().toLowerCase() === EMPTY_TOKEN)
    );
    if (filtered.length !== values.length || values[0] !== EMPTY_TOKEN) {
        opts.values = [EMPTY_TOKEN, ...filtered];
    }
}

/**
 * 对单个节点执行"默认空输入"处理。
 */
function applyEmptyDefault(node, state) {
    const widgets = findFileWidgets(node);
    if (!widgets.length) return;

    for (const widget of widgets) {
        hookWidgetInteraction(node, widget, state);
        setPlaceholder(widget);
        ensureNoneOption(widget);

        if (!state.guarding || state.userInteracted) continue;

        // 值已经是空的就什么都不用做。
        if (isEmptyToken(widget.value)) continue;

        // 值非空但用户没主动操作过 —— 这是前端自动填的
        // （异步拉完文件列表后把值设成第一个文件），拉回空值。
        //
        // 选中值设为字符串 "none"。
        //
        // 用 "none" 而不是 null 的原因：
        //   - null 在前端只显示空白，看不出这是"故意留空"；
        //   - 字符串 "none" 因为确实在 options 里，能正常匹配并显示
        //     出 label "none"，同时又是一个后端认得的空值标记。
        //
        // 注意："none" 必须已经在 options 里（见 ensureNoneOption），
        // 否则前端会认为这是个找不到的文件，反而回退显示第一个文件。
        widget.value = EMPTY_TOKEN;
    }
}

/**
 * 标记用户已经主动操作过这个节点。
 *
 * "默认空输入"的保护窗口只针对**被动**写入的前端自动填充
 * （前端异步拉文件列表后把值改成第一个文件）。
 * 用户一旦主动点下拉框选文件、或点上传按钮，
 * 就该以用户的选择为准 —— 此时不能再拦。
 */
function markUserInteraction(node) {
    const state = nodeState(node);
    state.userInteracted = true;
    // 直接解除保护窗口，比只置标志更彻底：
    // 上传成功后组合框的值必须能落下去。
    if (state.guarding) {
        state.stop?.();
    }
}

/**
 * 节点状态：记录是否处于保护窗口。
 */
function nodeState(node) {
    if (!node.__hervinaiState) {
        node.__hervinaiState = {
            guarding: false,
            userInteracted: false,
            timer: null,
            stop: null,
        };
    }
    return node.__hervinaiState;
}

function startGuard(node) {
    const state = nodeState(node);
    state.guarding = true;

    const stop = () => {
        state.guarding = false;
        if (state.timer) {
            clearInterval(state.timer);
            state.timer = null;
        }
    };
    state.stop = stop;

    applyEmptyDefault(node, state);

    // 保护窗口内持续轮询：前端异步拉取文件列表后会把值改成第一个文件，
    // 这里在它之后把它拉回空值。
    state.timer = setInterval(() => {
        if (!state.guarding) return;
        applyEmptyDefault(node, state);
    }, POLL_INTERVAL_MS);

    // 窗口结束后自动解除，之后用户可以自由选择文件
    setTimeout(stop, GUARD_WINDOW_MS);
}

/**
 * 来自已保存的工作流：若配置里带来了真实文件名，
 * 立即结束保护并把该值写回去，尊重工作流的选择。
 *
 * 注意两个坑：
 *
 * 1. LiteGraph 写入 widget 值与调用 onConfigure 的先后顺序不固定，
 *    两种情况都要覆盖——所以这里显式再写一次。
 *
 * 2. `widgets_values` 只收录 serialize !== false 的控件，
 *    而 `node.widgets` 收录全部控件。直接用 node.widgets 的下标
 *    去索引 widgets_values 会错位（我们给音频节点加的播放器
 *    和上传按钮都是 serialize:false，正好会顶掉后面的下标）。
 *    所以这里要用"只统计可序列化控件"的下标。
 */
function respectWorkflow(node, info) {
    const state = nodeState(node);
    const values = info?.widgets_values;
    if (!Array.isArray(values)) return;

    // 只有可序列化的控件才会出现在 widgets_values 里，
    // 它的下标 = 它在"可序列化控件序列"中的位置。
    const serializable = (node?.widgets ?? []).filter(
        (w) => w && w.serialize !== false
    );

    const saved = [];
    for (const widget of findFileWidgets(node)) {
        const idx = serializable.indexOf(widget);
        if (idx < 0) continue;
        const v = values[idx];
        if (v !== null && v !== undefined && !isEmptyToken(v)) {
            saved.push([widget, v]);
        }
    }

    if (!saved.length) return;

    // 工作流里的选择是用户明确保存的：结束保护窗口，
    // 让这个选择直接生效（而不是被"默认空输入"拉回 none）。
    state.userInteracted = true;
    state.stop?.();

    // 显式写回，确保写入时机早于本次回调时也能恢复
    for (const [widget, v] of saved) {
        try {
            widget.value = v;
        } catch (err) {
            /* 忽略写入失败 */
        }
    }
}

// ---------------------------------------------------------------------------
// 音频播放器 + 上传按钮
// ---------------------------------------------------------------------------

/**
 * 把 "subfolder/name" 拆成 [subfolder, filename]。
 * 等价于核心 audioUtils.splitFilePath。
 */
function splitFilePath(path) {
    const idx = path.lastIndexOf("/");
    if (idx === -1) return ["", path];
    return [path.substring(0, idx), path.substring(idx + 1)];
}

/**
 * 生成 /view 形式的资源 URL。
 * 等价于核心 audioUtils.getResourceURL。
 */
function getResourceURL(subfolder, filename, type = "input") {
    const params = [
        "filename=" + encodeURIComponent(filename),
        "type=" + type,
        "subfolder=" + subfolder,
        app.getRandParam().substring(1),
    ].join("&");
    return "/view?" + params;
}

/**
 * 更新播放器：设置音频源，并同步空态样式。
 *
 * 核心给音频播放器加了 `empty-audio-widget` class，
 * 前端 CSS 里 `audio.comfy-audio.empty-audio-widget{display:none}`
 * 会把没有内容的播放器隐藏掉。
 *
 * 注意：这里只改 element.src，不去写 audioUIWidget.value。
 * DOMWidgetImpl 的 value setter 会转手调用 options.setValue，
 * 而我们的 setValue 写的是"audio"组合框——一旦把 URL 写进去，
 * 组合框里的文件名就被污染了。播放器的值由 options.getValue
 * 从组合框实时推导即可。
 */
function updateAudioUIWidget(audioUIWidget, url = "") {
    try {
        audioUIWidget.element.src = url;
        if (url) {
            audioUIWidget.element.classList.remove("empty-audio-widget");
        } else {
            audioUIWidget.element.classList.add("empty-audio-widget");
        }
    } catch (err) {
        /* 忽略播放器更新失败 */
    }
}

/**
 * 创建文件选择器。等价于核心 useNodeFileInput。
 */
function useNodeFileInput(node, options) {
    const {
        accept,
        allow_batch = false,
        fileFilter = () => true,
        onSelect,
    } = options;

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = accept ?? "*";
    fileInput.multiple = allow_batch;

    fileInput.onchange = () => {
        const selected = fileInput.files?.length
            ? Array.from(fileInput.files)
            : [];
        // 重置 value，重复选同一个文件也能触发 onchange
        fileInput.value = "";
        const files = selected.filter(fileFilter);
        if (files.length) onSelect(files);
    };

    return {
        openFileSelection: () => fileInput.click(),
    };
}

/**
 * 上传文件到后端，并把新文件塞进下拉列表。
 *
 * 核心用的是 POST /upload/image（ComfyUI 的统一上传接口，
 * 音频/视频也走它，字段名就叫 image）。
 */
async function uploadAudioFile(node, audioWidget, audioUIWidget, file) {
    try {
        const body = new FormData();
        body.append("image", file);

        const resp = await api.fetchApi("/upload/image", {
            method: "POST",
            body,
        });

        if (resp.status !== 200) {
            console.warn(
                `[hervinai-none] 上传失败：${resp.status} - ${resp.statusText}`
            );
            return false;
        }

        const data = await resp.json();
        const path = data.subfolder
            ? `${data.subfolder}/${data.name}`
            : data.name;

        // 把新文件加进下拉列表
        const values = audioWidget?.options?.values;
        if (Array.isArray(values) && !values.includes(path)) {
            values.push(path);
        }

        const oldValue = audioWidget.value;
        updateAudioUIWidget(
            audioUIWidget,
            api.apiURL(getResourceURL(...splitFilePath(path)))
        );

        audioWidget.value = path;
        // 手动触发回调，让新前端的 Vue 组件也更新
        audioWidget.callback?.(path);
        node.onWidgetChanged?.(audioWidget.name, path, oldValue, audioWidget);

        // 上传是用户的明确意图：解除"默认空输入"保护，
        // 否则保护窗口内的写入会被守卫拦下（表现为"点了上传没反应"）。
        markUserInteraction(node);

        return true;
    } catch (err) {
        console.warn("[hervinai-none] 上传异常：", err);
        return false;
    }
}

/**
 * 阻止核心给本包的音频节点注入 `AUDIOUPLOAD` 控件。
 *
 * 背景见文件头注释第 3 节。核心的 `Comfy.UploadAudio` 扩展会：
 *
 *     if (input.required.audio[1].audio_upload === true)
 *         input.required.upload = ['AUDIOUPLOAD', {}]
 *
 * 而 `AUDIOUPLOAD` 控件的构造函数需要一个 `audioUI` 控件
 * （核心只给节点名硬编码匹配的那几个节点注入 `audioUI` 输入）。
 * 我们节点改名了，`audioUI` 控件不存在 → 构造函数崩 →
 * 节点加载不上画布。
 *
 * 这个函数要处理**两种扩展执行顺序**：
 *
 * 1. 核心扩展先跑（通常如此）：它已经把 `upload` 注入到
 *    `input.required` 里了 —— 我们把 `upload` 删掉；
 * 2. 本扩展先跑：核心随后会看到 `audio_upload` 标记并注入
 *    `upload` —— 我们把 `audio_upload` 标记摘掉。
 *
 * 两种情况都做了处理，顺序无关。
 *
 * 顺便也把 `audioUI` 输入删掉：核心不会给我们注入它
 * （节点名不匹配），但如果别的扩展注入了，
 * 它同样会导致一个找不到控件的悬空插槽。
 */
function disableCoreAudioUpload(nodeData) {
    try {
        const input = nodeData?.input;
        if (!input || typeof input !== "object") return;

        // 1) 摘掉 audio_upload 标记 —— 阻止核心注入 AUDIOUPLOAD 控件。
        //
        //    服务端返回的是 V1 兼容视图：audio 的定义是
        //        ["<选项列表>", { default, multiselect, options, audio_upload: true }]
        //    标记就在这个字典里（与 options 平级）。
        //
        //    同时兼容 V2 形状（标记被 splat 到 spec 顶层）：
        //        { type: "COMBO", name: "audio", audio_upload: true, options: [...] }
        const required = input.required;
        if (required && typeof required === "object") {
            const spec = required.audio;
            if (Array.isArray(spec) && spec.length > 1 && spec[1] &&
                typeof spec[1] === "object") {
                delete spec[1].audio_upload;
            }
            if (spec && !Array.isArray(spec) && typeof spec === "object") {
                delete spec.audio_upload;
            }

            // 2) 删掉核心已经注入的 upload 输入 ——
            //    阻止 AUDIOUPLOAD 控件被建出来（它的构造函数会崩）
            if ("upload" in required) {
                delete required.upload;
            }

            // 3) 删掉 audioUI 输入 ——
            //    它依赖 AUDIO_UI 控件。核心只按节点名硬编码注入，
            //    我们改名后拿不到；留着就是个悬空插槽。
            //    （我们自己在 onNodeCreated 里建同名控件，功能等价。）
            if ("audioUI" in required) {
                delete required.audioUI;
            }
        }

        // input_order 里也可能残留了这些名字，一并清掉，
        // 否则 getOrderedInputSpecs 会去查一个已经不存在的输入
        const order = nodeData?.input_order;
        if (order && typeof order === "object") {
            for (const section of ["required", "optional"]) {
                const list = order[section];
                if (!Array.isArray(list)) continue;
                order[section] = list.filter(
                    (n) => n !== "upload" && n !== "audioUI"
                );
            }
        }
    } catch (err) {
        console.warn("[hervinai-none] 禁用核心音频上传控件失败：", err);
    }
}

/**
 * 给音频节点建播放器 + 上传按钮。
 *
 * 全部用 try/catch 包住：即使前端 API 变动，也只是少个预览，
 * 绝不能影响节点本身加载到画布。
 */
function addAudioControls(node) {
    if (node.__hervinaiAudioReady) return;
    node.__hervinaiAudioReady = true;

    try {
        const audioWidget = node.widgets?.find((w) => w.name === "audio");
        if (!audioWidget) return;

        // --- 播放器 ---
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.classList.add("comfy-audio");
        audio.setAttribute("name", "media");

        // 控件名必须叫 "audioUI"——和核心自己的 AUDIO_UI 控件完全一致。
        //
        // 原因：核心的 Comfy.AudioWidget 扩展在**执行之后**还会按名字
        // 找这个控件来刷新播放器：
        //
        //     onNodeOutputsUpdated(outputs) {
        //       for (const [id, out] of Object.entries(outputs)) {
        //         if (!out.audio?.length) continue;
        //         const node = findNode(id);
        //         const widget = node.widgets?.find(w => w.name === 'audioUI');
        //         updateUIWidget(widget, url);   // widget 为 undefined → 崩
        //       }
        //     }
        //
        // 本节点输出 AUDIO，所以这段每次执行都会跑。控件找不到就抛
        // TypeError（被 invokeExtensions 的 try/catch 吞掉，但会刷一片
        // 控制台错误）。用 "audioUI" 命名即可让它找到我们建的播放器。
        //
        // 顺带说明：核心的 AUDIO_UI 控件本身就是
        //     e.addDOMWidget(name, 'audioUI', element)
        // ——name 和 type 都是 "audioUI"，所以这不是"保留标识"，
        // 而是核心约定的名字。核心自己也是这么建的。
        //
        // 注意与另一处区分：core 的 AUDIOUPLOAD 控件是在**节点构造函数**
        // 里创建的（addInputs → addInputWidget），那时本控件还不存在，
        // 所以它崩溃。所以 upload 输入必须由 disableCoreAudioUpload 删掉，
        // 绝不能让核心来建这个控件。
        const audioUIWidget = node.addDOMWidget(
            "audioUI",
            "audioUI",
            audio,
            {
                // 和核心的 AUDIO_UI 一样：只在画布上显示。
                surfaces: {
                    canvas: "shown",
                    vueNode: "never",
                    panel: "never",
                },
            }
        );
        audioUIWidget.serialize = false;
        audioUIWidget.options.serialize = false;

        // 播放器显示的 URL 由"audio"组合框推导：
        //   audio 组合框 -> 文件名 -> /view?filename=... -> <audio>.src
        const currentUrl = () => {
            const value = audioWidget.value;
            return value && !isEmptyToken(value)
                ? api.apiURL(getResourceURL(...splitFilePath(value)))
                : "";
        };

        audioUIWidget.options.getValue = () => currentUrl();
        audioUIWidget.options.setValue = (v) => {
            if (typeof v === "string" && v) {
                audio.src = v;
            }
        };

        // --- 同步播放器内容 ---
        const syncPlayer = () => {
            updateAudioUIWidget(audioUIWidget, currentUrl());
        };

        syncPlayer();

        const prevCallback = audioWidget.callback;
        audioWidget.callback = function () {
            prevCallback?.apply(this, arguments);
            syncPlayer();
        };

        // 恢复已保存的工作流后也要同步一次。
        //
        // 两条路径都要覆盖：
        //   - onConfigure：LiteGraph 恢复节点配置时调用，
        //     此时 combo 的值才被写回来；
        //   - onGraphConfigured：整张图配置完成后调用。
        //
        // 少了任何一条，都会出现"工作流里选了音频，打开后播放器是空的"。
        const prevOnConfigure = node.onConfigure;
        node.onConfigure = function () {
            const result = prevOnConfigure?.apply(this, arguments);
            syncPlayer();
            return result;
        };

        const prevOnGraphConfigured = node.onGraphConfigured;
        node.onGraphConfigured = function () {
            prevOnGraphConfigured?.apply(this, arguments);
            syncPlayer();
        };

        // --- 上传按钮 ---
        const { openFileSelection } = useNodeFileInput(node, {
            accept: "audio/*",
            fileFilter: (file) => file.type.startsWith("audio/"),
            onSelect: async (files) => {
                if (!files.length) return;

                // 用户主动点了上传：结束"默认空输入"保护，
                // 否则保护窗口内的写入会被当成"前端自动填充"而拉回 none。
                markUserInteraction(node);

                const previousValue = audioWidget.value;
                audioWidget.value = files[0].name;
                const ok = await uploadAudioFile(
                    node,
                    audioWidget,
                    audioUIWidget,
                    files[0]
                );
                if (!ok) {
                    audioWidget.value = previousValue;
                }
                node.graph?.setDirtyCanvas(true);
            },
        });

        const uploadWidget = node.addWidget(
            "button",
            "upload",
            "audio",
            () => openFileSelection(),
            {
                serialize: false,
                surfaces: {
                    canvas: "shown",
                    vueNode: "never",
                    panel: "never",
                },
            }
        );
        // 注意：options.serialize 不会自动提升到 widget.serialize，
        // 前端判断"要不要序列化"看的是顶层属性，所以必须显式再设一次。
        // 否则这个按钮会往工作流的 widgets_values 里塞一条，
        // 把后面控件的下标全部顶掉一位。
        uploadWidget.serialize = false;
        uploadWidget.label = "choose file to upload";

        // 注意：这里**不**设置 node.previewMediaType = "audio"。
        //
        // previewMediaType 是前端用来判断"这个节点要渲染哪种预览"的标记，
        // 音频节点被识别后会走 isAudioNode() 那条专门的渲染分支，
        // 而那条分支是按核心节点名硬编码匹配的（见 useMoreOptionsMenu 里
        // LoadAudio / LoadImage / LoadVideo 的写死映射），改名后的节点
        // 匹配不上，反而会出问题。
        //
        // 思路参考了社区节点包 WhatDreamsCost-ComfyUI 的 LoadAudioUI：
        // 它也没有设这个值，播放器完全是自己 addDOMWidget 建的 DOM 控件，
        // 不依赖前端的预览通道。
        // （本包不依赖那个项目，只是做法上得到了启发。）
    } catch (err) {
        console.warn("[hervinai-none] 添加音频控件失败：", err);
    }
}

app.registerExtension({
    name: "hervinai-none.empty-default",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        // 只处理本包的节点。
        // 双重判断：分类名 + 节点名白名单。
        // （V3 节点的 category 来自 schema，这里已由后端设为 hervinai-none；
        //   但万一后端没能改写，用节点名兜底。）
        const category = String(nodeData?.category || "");
        const name = String(nodeData?.name || "");
        const isOurs =
            category.includes("hervinai-none") ||
            OWN_NODE_CLASSES.includes(name);
        if (!isOurs) return;

        const proto = nodeType.prototype;

        // 标记这是音频节点，供 onNodeCreated 判断
        if (name === AUDIO_NODE_CLASS) {
            proto.__hervinaiIsAudio = true;

            // 关键：切断核心的 AUDIOUPLOAD 控件。
            //
            // 核心的 Comfy.UploadAudio 扩展看到 audio_upload 标记就会
            // 注入 AUDIOUPLOAD 控件，而那个控件的构造函数需要一个
            // audioUI 控件——核心只给节点名硬编码匹配的那几个节点注入
            // audioUI，我们改名了，拿不到 → 构造函数抛
            // "Cannot read properties of undefined (reading 'element')"
            // → 节点加载不上画布。
            //
            // 播放器 + 上传按钮由本补丁在 onNodeCreated 里自己建，
            // 不依赖核心。
            disableCoreAudioUpload(nodeData);
        }

        const onNodeCreated = proto.onNodeCreated;
        proto.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            try {
                startGuard(this);
            } catch (err) {
                console.warn("[hervinai-none] 初始化默认值失败：", err);
            }
            // 音频节点补播放器 + 上传按钮
            if (this?.__hervinaiIsAudio) {
                try {
                    addAudioControls(this);
                } catch (err) {
                    console.warn("[hervinai-none] 添加音频控件失败：", err);
                }
            }
            return result;
        };

        const onConfigure = proto.onConfigure;
        proto.onConfigure = function (info) {
            const result = onConfigure?.apply(this, arguments);
            try {
                respectWorkflow(this, info);
            } catch (err) {
                console.warn("[hervinai-none] 处理工作流配置失败：", err);
            }
            return result;
        };

        console.log("[hervinai-none] 前端补丁已加载：", nodeData?.name);
    },
});
