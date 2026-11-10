# comfyui-hervinai-none

**English** | [中文](README.md)

Make ComfyUI's **Load Image / Load Audio / Load Video** tolerate an **empty file selection**.

When the file dropdown has no file chosen, the node no longer raises an error — it outputs
`None` on every socket instead. That makes the load nodes safe to drop into conditional
workflows: a branch with no asset simply goes dead, without tearing down the whole pipeline.

**Core nodes are left completely untouched.** This package does not override or modify
`LoadImage` / `LoadAudio` / `LoadVideo`; it only adds three new standalone nodes.
Deleting the directory uninstalls it.

![ComfyUI](https://img.shields.io/badge/ComfyUI-Custom%20Node-3b82f6)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **No error on empty input** — outputs `None` instead of raising and killing the pipeline
- **Empty by default** — dropping the node on the canvas does not auto-fill the first file
- **Core nodes untouched** — only adds the three `*Hervinai` nodes; core `LoadImage` /
  `LoadAudio` / `LoadVideo` behave exactly as before
- **Previews preserved** — image, video and audio previews all still work
- **Zero new dependencies** — uses only numpy / torch / Pillow / av, all already in core
- **Extra outputs on the video node** — `IMAGE` (decoded frames) and `AUDIO` (the sound
  track), plus a set of VHS-style frame processing parameters

---

## Nodes

| Node name (for search) | Display name | Category | Outputs |
|---|---|---|---|
| `LoadImageHervinai` | Load Image Hervinai (empty-input safe) | `hervinai-none` | `IMAGE`, `MASK` |
| `LoadAudioHervinai` | Load Audio Hervinai (empty-input safe) | `hervinai-none` | `AUDIO` |
| `LoadVideoHervinai` | Load Video Hervinai (empty-input safe) | `hervinai-none` | `VIDEO`, `IMAGE`, `AUDIO` |

Just type `Hervinai` in the node search box.

> Besides `VIDEO`, `LoadVideoHervinai` also outputs `IMAGE` (the decoded frames) and
> `AUDIO` (the video's sound track), and carries a set of VHS-style frame parameters
> (frame rate / size / frame selection). See the "Video frames as IMAGE output",
> "Audio track as AUDIO output" and "Frame processing parameters" sections below.

---

## Installation

### Option 1: ComfyUI Manager (recommended)

Search for `comfyui-hervinai-none` in ComfyUI Manager and click Install.

### Option 2: git clone

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/henry19801109/comfyui-hervinai-none.git
```

### Option 3: manual download

Download the source archive from
[Releases](https://github.com/henry19801109/comfyui-hervinai-none/releases) and extract it to
`ComfyUI/custom_nodes/comfyui-hervinai-none/`:

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

**Fully restart ComfyUI** after installing (the frontend extension needs a reload —
refreshing the browser page is not enough). The console printing `[hervinai-none]`
log lines means it loaded.

This package adds **no new dependencies** — only numpy / torch / Pillow / av, all
already shipped with ComfyUI core.

---

## Behaviour

### What counts as "empty input"

All of these are treated as empty input, output `None`, and never raise:

- `None`
- an empty string `""`
- whitespace only `"   "`
- the placeholder item `"none"` / `"NONE"` (case-insensitive)

### Empty by default

When you drop any of the three nodes onto the canvas, the file dropdown **starts empty** —
it does not auto-select some file from the `input` directory.

Technically, "empty" has to satisfy two conditions at once:

1. **`none` is the first entry** of the dropdown's option list (inserted by the backend),
   and `default` points at it;
2. **the selected value is `none`** (set by the frontend).

The reason is how the frontend's `WidgetSelect` component renders: if the selected value
matches an entry in `options.values` it shows that entry's label; if it doesn't match, it
shows the raw value. If you only set the selected value without putting `none` into the
option list, the frontend treats it as a "missing file" option and falls back to displaying
the first real file.

> Core `LoadVideo` / `LoadAudio` option lists have **no** `none` placeholder to begin with,
> so this package has to add it.

### When a real file is selected

Behaviour is **identical to the core nodes**, including the file-existence check.
If the selected file has been deleted from disk, or if parsing fails because the file is
corrupt, the node degrades to outputting `None` and logs a warning instead of crashing
the whole job.

### Behaviour check

With no file uploaded, all outputs of the three nodes really are `None`:

| Input | Outputs |
|---|---|
| `none` (placeholder item) | `(None, None)` / `(None,)` / `(None, None)` |
| `None` | same as above |
| `""` (empty string) | same as above |
| a real file selected | the real data |

### Video frames as IMAGE output

Besides `VIDEO`, `LoadVideoHervinai` has an extra `IMAGE` output you can connect straight
to `VAE Encode`, `Save Image`, and friends.

**The frame extraction path is exactly the one the official `Get Video Components` uses.**
The core node that splits a `VIDEO` into `IMAGE` / `AUDIO` is `GetVideoComponents`, and it
reads `video.get_components().images`. This package follows that **same** path, so pixel
format, rotation metadata and 10-bit / HDR handling all match core exactly — no
"colours don't line up" and no "the video is sideways".

The decoded tensor has shape `[frames, height, width, channels]`, `float32`, values in
`0~1` — the standard ComfyUI `IMAGE` type.

If not a single frame can be decoded (empty file / corrupt file), core returns an empty
tensor like `torch.zeros(0,0,0,3)`. This package normalises that to `None`, so downstream
nodes never receive a weird zero-frame `IMAGE`.

**`max_frames` caps how many frames are decoded.**

The node has one optional input, `max_frames`, default `16`:

| Value | Meaning |
|---|---|
| `16` (default) | decode at most 16 frames |
| `0` | **unlimited** — decode every frame of the video |
| any other number | decode at most that many frames |

> **Why 16 rather than everything, by default.**
> One 1080p RGB float32 frame is `1920 × 1080 × 3 × 4 ≈ 24 MB`.
> A 10-second 30fps video is 300 frames ≈ **7.4 GB** — decoding everything
> would almost certainly exhaust memory. 16 frames is roughly 380 MB, which
> virtually any machine can handle.

Implementation-wise, when a frame limit is in effect the time window is first shortened
with `as_trimmed()`, so core's decoder **stops after decoding that many frames** rather
than decoding everything and then throwing most of it away. A contiguous window is the
friendliest possible access pattern for a video codec (sequential decode) and is far
faster than random seeking frame by frame.

> Frame extraction is **extra** work: even if you only consume the `VIDEO` output, the
> node still decodes frames. That's because V3 nodes have no "lazy per-output evaluation" —
> all outputs of a node are computed together in a single execution.
> If the video is long and you don't need `IMAGE`, lower `max_frames` (e.g. to `1`), or
> just use the core `Load Video` node.

**Changing `max_frames` re-runs the node.**

Core's fingerprint only looks at the file modification time, so this package folds the
`max_frames` value into the fingerprint too — otherwise ComfyUI would consider nothing
changed and skip execution.

### Audio track as AUDIO output

The third output of `LoadVideoHervinai` is `AUDIO` — the video's sound, connectable
directly to `Save Audio`, `Preview Audio`, and friends.

**The audio track is clipped to the same window as the extracted frames.**

In other words, the audio is not the whole track: it covers the **same time window** as the
decoded picture:

| Node parameter | Effect on the audio track |
|---|---|
| `skip_first_frames` | the track starts at the timestamp of that frame |
| `max_frames` | track length = the duration of the frame-extraction window |
| `force_rate` | no effect (it only changes the temporal density of the picture) |
| `select_every_nth` | no effect (it only changes how sparsely frames are picked) |

For example, in a 30fps video with `max_frames=16` and `skip_first_frames=0`, the audio
track is the first `16/30 ≈ 0.533` seconds. With `max_frames=60` it becomes the first
2 seconds.

**Why they line up naturally.**

Core's `get_components()` decodes picture and sound **in a single pass**, and it already
clips the sound to that window internally (in `get_components_internal` it skips the head
by `to_skip` and then truncates the tail by `int(duration * sample_rate)`).

So this package does **not** decode the audio a second time — picture and sound come from
the same decode. That saves half the time and guarantees the two are strictly aligned,
never out of sync. This matches what VHS does (VHS also uses
`lazy_get_audio(video, start_time, frame_load_cap * frame_duration)`).

**The audio format** is the same as core's `Load Audio` output:

```
{
    "waveform": torch.Tensor,   # shape [B, channels, samples], float32, values -1~1
    "sample_rate": int          # sample rate
}
```

**If the video has no audio track, this output is `None`** (not an empty tensor, and no
error). For a video without audio, `components.audio` is already `None` in core and this
package passes it through unchanged.

> **Degradation:** in the unlikely event that your core version has no `io.Audio` (the
> AUDIO output type was added later), the node automatically keeps only its `VIDEO` and
> `IMAGE` outputs and **does not** break the whole package.

### Frame processing parameters (VHS-style)

Besides `max_frames`, the node carries a set of VideoHelperSuite-style frame processing
parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `force_rate` | `0` | Force a frame rate. `0` = keep the original rate; any other value = resample the frame sequence to that rate |
| `custom_width` | `0` | Target width. `0` = keep the original width |
| `custom_height` | `0` | Target height. `0` = keep the original height |
| `skip_first_frames` | `0` | Number of frames to skip at the beginning |
| `select_every_nth` | `1` | Take every n-th frame. `1` = take all of them |

**These parameters affect the IMAGE and AUDIO outputs; the VIDEO output stays the
original video.** That keeps the on-canvas video preview intact — rebuilding the video
would lose the original sound track and would have to re-encode for the preview, which
is far too expensive.

> So `VIDEO` and `IMAGE` describe the same video, but **the frames may not correspond
> one-to-one**: `VIDEO` is the original, `IMAGE` is the frame sequence after the
> processing above. When you need the two to match exactly, split the VIDEO with core's
> `Get Video Components` node.
>
> `AUDIO` is the sound track clipped to the frame window (see "Audio track as AUDIO
> output" above), matching the time window of the IMAGE output.

**Two deliberate differences from VHS semantics:**

1. **No 8-pixel alignment.** VHS's `target_size()` rounds width and height up to a
   multiple of 8 (for encoder compatibility); this package doesn't. Enter 1000 and you
   get 1000 — an aligned value that's quietly different from what you typed is confusing.
2. **Filling in only one dimension preserves the aspect ratio** (this one matches VHS):
   set only `custom_width` and the height is computed from the original ratio; set only
   `custom_height` and the width follows the ratio; set both and the frames are stretched
   to exactly that size.

**Three implementation details:**

- **The processing order matches VHS: skip the head → resample the frame rate → take
  every n-th frame → cap the frame count.** The rate resampling happens *before* the
  every-n-th selection, at the same place the `fps=` filter sits in VHS.
- `skip_first_frames` and `select_every_nth` are applied **during extraction**, not after
  the fact. The decoder then only decodes the frames that are actually needed — e.g. to
  get 10 frames with every-3rd selection it only decodes a 30-frame window, instead of
  decoding the whole video and discarding 95% of it.
- `force_rate` uses nearest-timestamp resampling: when lowering the rate frames are
  dropped, when raising it existing frames are repeated (the source video never had more
  frames to begin with). The resampling produces an **exact** frame count for the target
  rate, so how much the decode window happens to over- or under-reads doesn't affect the
  final count; `max_frames` can still cap the count after resampling.

**Changing any of these parameters re-runs the node.**
All of their values are folded into the fingerprint.

### About downstream nodes

Once this node outputs `None`, whether a **downstream** node errors depends on that
downstream node. Most downstream nodes raise a type error when they receive `None` —
that's standard ComfyUI behaviour and this package doesn't add any extra interception.
Common approaches:

- branch with an `If` / `Impact Switch`-style node so the `None` takes another route;
- or place a passthrough node that accepts any input type in the chain as a placeholder.

---

## Implementation notes

### Two code paths: core's V1 and V3 node styles

Recent ComfyUI has migrated `LoadVideo` / `LoadAudio` to the V3 schema
(`io.ComfyNode`), while `LoadImage` is still V1. The key differences between the two
styles:

| | V1 | V3 |
|---|---|---|
| Input description | `INPUT_TYPES()` classmethod | `define_schema()` returning an `io.Schema` |
| `INPUT_TYPES` | overridable | **`@final`, cannot be overridden** |
| Execution entry point | `FUNCTION` points at an instance method | `FUNCTION` is the classmethod `EXECUTE_NORMALIZED` |
| Validation hook | `VALIDATE_INPUTS` | `validate_inputs` (lowercase) |
| Fingerprint | `IS_CHANGED` | `fingerprint_inputs` |

So this package follows each core node's actual style down one of two paths:

- **V1 path** — subclass the core class, override `INPUT_TYPES` to insert `none` at the
  head of the options, override `VALIDATE_INPUTS` to let empty input through, override
  `IS_CHANGED` to skip hashing on empty input, then wrap it in a runner that turns empty
  input into `None` outputs.
- **V3 path** — subclass the core class, override `define_schema` to inject the
  placeholder, override `execute` (a classmethod) to turn empty input into
  `io.NodeOutput(None, ...)`, and override `validate_inputs` and `fingerprint_inputs`.

### A few easy traps

- **`INPUT_TYPES` is `@final` on V3.** You can't override it the V1 way — either the
  startup errors out or the change silently doesn't take. To add a placeholder you have
  to go through `define_schema`.
- **V3's `FUNCTION` is `EXECUTE_NORMALIZED`.** It calls `cls.execute(**kwargs)`
  internally, and note that it's a **classmethod**, not `core_func(self, ...)`. The
  empty-input check has to live in the overridden `execute`.
- **V3's `CATEGORY` / `display_name` / `node_id` are `@final` classproperties** whose
  values can only come from the `schema`. Leave them alone and the node shows up as
  "Load Video" under core's `video` category, and the frontend patch — which identifies
  this package's nodes by category name — stops working. This package rewrites all
  three explicitly inside `define_schema`.
- **Don't rebuild `Combo.Input` — mutate it in place.** Rebuilding means copying the
  constructor's parameter names, and those differ between ComfyUI versions (newer ones
  add `raw_link` / `advanced`), so copying one version's signature onto another raises
  `TypeError` and fails the node's registration entirely. Mutating the `options` and
  `default` attributes in place is version-independent.
- **Short-circuit the fingerprint first.** `fingerprint_inputs` (V3) / `IS_CHANGED` (V1)
  are called **before** `execute` and will hash the file or stat its modification time.
  On empty input the file doesn't exist at all, so calling them directly raises and
  floods the log with warnings. This package returns a constant on empty input.
- **The classproperty cache on the V3 base class must not be inherited** (this one is
  sneaky, and it shows up as `list index out of range`):

  ```python
  _RETURN_TYPES = None
  @final
  @classproperty
  def RETURN_TYPES(cls):
      if cls._RETURN_TYPES is None:
          cls.GET_SCHEMA()      # computed and cached on first access
      return cls._RETURN_TYPES
  ```

  Core's `comfy_extras` loads before `custom_nodes`, so core `LoadVideo`'s
  `_RETURN_TYPES` has already been filled in as `["VIDEO"]`. A subclass we build from
  the core class has **no** `_RETURN_TYPES` of its own, so the attribute lookup walks up
  the MRO and finds core's cached value.

  The frontend and the backend then disagree:

  | | Source of truth | What it sees |
  |---|---|---|
  | Frontend (`object_info`) | `GET_NODE_INFO_V1()` → `schema.outputs` | VIDEO + IMAGE + AUDIO ✅ |
  | Backend validation | `RETURN_TYPES` (the inherited cache) | VIDEO only ❌ |

  ComfyUI's validation code looks up the upstream node's output types by slot index:

  ```python
  r = nodes.NODE_CLASS_MAPPINGS[o_class_type].RETURN_TYPES
  received_type = r[val[1]]        # frontend wired slot 1 → IndexError
  ```

  The frontend wires two outputs, the backend thinks there's one → out of bounds →
  `Exception when validating node: list index out of range`.

  **The fix**: when building the subclass, reset those cached attributes to `None` in
  the subclass's own `__dict__` so they get recomputed from our own `define_schema`.
  Only the subclass's attributes are reset — **core's cache is completely unaffected**
  (core's own `LoadVideo` isn't polluted). This package handles them uniformly in
  `_reset_v3_caches()`, covering 16 attributes such as `_RETURN_TYPES` /
  `_RETURN_NAMES` / `_OUTPUT_IS_LIST` / `_OUTPUT_TOOLTIPS` / `_CATEGORY` /
  `_DESCRIPTION`.

### Others

- **The file field name is auto-detected.** The three core nodes don't agree on the
  field name — Load Image calls it `image`, Load Audio calls it `audio`, and **Load Video
  calls it `file`**. At load time this package locates the real field by looking for the
  `*_upload` marker in the core class's input definitions. Nothing is hard-coded, so a
  core rename won't break it.
- **The V3 upload marker isn't an attribute — it's computed on the fly.**
  Core's `Combo.Input` doesn't store `audio_upload` as an attribute; it computes it in
  `as_dict()`:

  ```python
  def as_dict(self):
      return super().as_dict() | prune_dict({
          ...
          **({self.upload.value: True} if self.upload is not None else {}),
      })
  ```

  So trying to drop `audio_upload` with `setattr(item, "audio_upload", False)` does
  **nothing** — the real switch is `self.upload`. This package clears it, so the marker
  is never generated.
- **Extra inputs must be stripped from kwargs before calling core.**
  Core's `execute` / `validate_inputs` / `fingerprint_inputs` only accept the one `file`
  argument. Passing an extra `max_frames` is an immediate `TypeError`. This package
  records the ids of all added inputs when building the node and filters them out
  before calling core.
- **Extra input values must be folded into the fingerprint.**
  Core's fingerprint only looks at the file modification time. Change `max_frames`
  without changing the fingerprint and ComfyUI decides "nothing changed" and skips
  execution — the parameter appears to do nothing.
- If the core class is missing (a trimmed-down install), the package falls back to a
  built-in minimal implementation so it never fails to load as a whole.
- Registration goes through the two module-level variables `NODE_CLASS_MAPPINGS` and
  `NODE_DISPLAY_NAME_MAPPINGS`, which is ComfyUI's convention for custom node packages.

### Frontend patch (web/hervinai_none.js)

Core's frontend provides a preview player and an upload button for the video / audio
nodes, but two things need patching.

**1. Default value**

The video / audio file widgets fetch the `input` directory listing **asynchronously**
when the node is created, then set the selected value to the first file in that list,
overwriting whatever this package set.

The patch takes over that step with **"user-interaction detection + polling correction"**:

- it hooks the widget's `setValue` and `callback`; as soon as a **non-empty** value
  shows up it assumes the user deliberately picked it (choosing from the dropdown, or
  clicking upload) and immediately ends the protection window, from then on deferring to
  the user's choice;
- during the protection window it scans every 100 ms: if the value is non-empty but the
  user hasn't acted yet, the frontend auto-filled it, so it's pulled back to `none`.

**Why it doesn't intercept `value` writes directly.**
The frontend's `ComboWidget.setValue` runs in this order:

```js
this.value = a;              // value first
this.callback?.(a, ...);     // callback second
```

If you use `defineProperty` to intercept the assignment, then when the user just picks a
file in the dropdown during the protection window the write gets **swallowed on the
spot** — by the time the callback could patch things up it's already too late, and what
the user sees is "I picked a file and nothing happened". The same happens to the filename
written when the upload button is clicked. So the patch only observes; it never
intercepts.

The patch also makes sure `none` really is in `options.values` and in first position —
the backend already does this, and repeating it here covers the case of "the backend is
an older version / the frontend got the data before the backend did", and also fixes the
case where core puts the placeholder in the middle of the list.

**2. Audio player + upload button**

Core's frontend has **two** audio extensions, each handling one half:

```js
// Comfy.AudioWidget — injects the player, keyed on the NODE NAME
if (['LoadAudio','SaveAudio','PreviewAudio','SaveAudioMP3',
     'SaveAudioOpus','SaveAudioAdvanced'].includes(comfyClass))
    input.required.audioUI = ['AUDIO_UI', {}]

// Comfy.UploadAudio — injects the upload button, keyed on the INPUT MARKER
if (input.required.audio[1].audio_upload === true)
    input.required.upload = ['AUDIOUPLOAD', {}]
```

The two conditions handle **different halves**, so a renamed node falls into the gap:

- the node name `LoadAudioHervinai` isn't on the allowlist → core doesn't inject the
  `audioUI` input → there's **no** `audioUI` widget on the canvas;
- but the backend inherited core `LoadAudio`'s `audio_upload` marker → core injects the
  `upload` input → there **is** an `AUDIOUPLOAD` widget on the canvas.

And the very first thing the `AUDIOUPLOAD` widget's constructor does is look for that
nonexistent `audioUI`:

```js
AUDIOUPLOAD(node) {
  const audioUI = node.widgets?.find(w => w.name === 'audioUI');  // undefined
  const update = () => updateUIWidget(audioUI, url);  // audioUI.element → crash
  update();   // runs immediately
}
```

`updateUIWidget`'s first line is `widget.element.src = url`, so it throws:

```
TypeError: Cannot read properties of undefined (reading 'element')
    at updateUIWidget (core-*.js)
    at onAudioWidgetUpdate (core-*.js)
    at AUDIOUPLOAD (core-*.js)
    at addInputWidget (app-*.js)
    at addInputs (app-*.js)
    at new ComfyNode (app-*.js)
```

The node **fails to load onto the canvas** (you can find it in the search box, but
dragging it in throws).

**The fix:** don't let core inject `AUDIOUPLOAD`; build the player and the upload button
in this package's own patch instead. The frontend `disableCoreAudioUpload()` and the
backend marker removal work together, so they hold under **either** extension ordering:

| Ordering | Who handles it |
|---|---|
| Core's extension runs first | the frontend deletes the already-injected `upload` / `audioUI` inputs |
| This extension runs first | the backend + frontend drop the `audio_upload` marker, so core never injects it |

The patch builds the widgets directly in `onNodeCreated`:

- player: `node.addDOMWidget("audioUI", "audioUI", <audio controls>)`, with the playback
  URL derived live from the `audio` combo's value (`/view?filename=...`);
- upload button: `node.addWidget("button", "upload", ...)`, going through
  `POST /upload/image` (ComfyUI's unified upload endpoint) and inserting the new file
  into the dropdown on success.

**The player widget's name must be `audioUI`, as both name and type.**
This matters because core looks the widget up **by name** after execution to refresh the
player:

```js
// Comfy.AudioWidget.onNodeOutputsUpdated
for (const [id, out] of Object.entries(outputs)) {
  if (!out.audio?.length) continue;
  const node = findNode(id);
  const widget = node.widgets?.find(w => w.name === 'audioUI');  // not found → crash
  updateUIWidget(widget, url);
}
```

This node outputs AUDIO, so this runs on every execution. A mismatched widget name throws
a `TypeError` (swallowed by `invokeExtensions`' try/catch, but it floods the console
with errors).

Core's own `AUDIO_UI` widget is built the same way:

```js
const widget = e.addDOMWidget(name, 'audioUI', element);   // name and type are both audioUI
```

So `audioUI` isn't a "reserved identifier" — it's a name core has agreed on. Just follow
the convention.

> Conversely, core's `AUDIOUPLOAD` widget is created in the **node constructor**
> (`addInputs` → `addInputWidget`), at which point the widgets this patch built don't
> exist yet. So the `upload` input **must** be removed — core must never build that
> widget. These two things are a matched pair: name the widget `audioUI` (so core can
> find it after execution), but delete the `upload` input (so core doesn't build the
> widget that crashes in the constructor).

**Why `node.previewMediaType = "audio"` is not set.**
`previewMediaType` is the frontend's marker for "this node wants this kind of preview".
A node identified as an audio node takes a dedicated render branch, and that branch is
matched against **hard-coded core node names** (`LoadAudio` / `LoadImage` / `LoadVideo`
are written into `useMoreOptionsMenu`), so a renamed node doesn't match.
The reference `LoadAudioUI` implementation from the community node package
`WhatDreamsCost-ComfyUI` doesn't set this value either — its player is a plain DOM widget
it builds with `addDOMWidget`, independent of the frontend's preview channel.
(This package doesn't depend on that project; it just drew inspiration from it.)

**Why it doesn't inject an `AUDIO_UI` input into the node definition.**
When the frontend renders a node it first runs `addInputSocket` to create a socket for
each input, then runs `addInputWidget` to find the matching widget. Only **registered**
widget types are claimed by `addInputWidget`, while `addInputSocket` happily creates a
socket for an **unknown** type (just without a widget bound to it). In other words,
stuffing an unregistered type name (like `["AUDIO_UI", {}]`) into the definition leaves a
dangling socket, the node throws while rendering, and it **fails to load onto the
canvas**. Building the widget in `onNodeCreated` doesn't touch the definition at all, so
the problem can't arise.

> Both new widgets explicitly set `serialize = false`, so they don't end up in
> `widgets_values` and don't shift the indices of the widgets after them.
> Note: putting it only in the `addWidget` options is not enough — the frontend checks
> the **top-level** `widget.serialize` property and won't promote it from
> `options.serialize`.

The video player is unaffected: it's rendered based on the **output type `VIDEO`**, not
on the node name.

**Only `audio_upload` is removed; `image_upload` / `video_upload` are kept.**
Core's upload button (`useImageUploadWidget()`) is only created when the
`image_upload` / `video_upload` markers are present, and it also requires
`imageInputName` to exist:

```js
function isImageUploadInput(e) {
  let t = e[1];
  return t !== void 0 && typeof t === 'object'
      && 'imageInputName' in t && typeof t.imageInputName === 'string';
}
// at construction time:
if (!isImageUploadInput(n)) throw Error('Image upload widget requires imageInputName augmentation');
```

So of those three markers, **only `audio_upload` is problematic** — removing it would
degrade the image / video nodes to a bare dropdown with no upload button.

**3. Loading an existing workflow**

The patch reads `widgets_values` in `onConfigure` to recognise real filenames. When it
finds one it ends the protection and restores the value, so it won't wipe a file you've
already selected in your workflow.

`widgets_values` only contains widgets whose `serialize !== false`, while `node.widgets`
contains all of them — so when computing indices you have to work from the "serialisable
widget sequence", not the raw `node.widgets` indices.

### Troubleshooting

On startup the console prints the field names and node style this package actually
used. The log messages themselves are in Chinese (the backend logs in the package's
original language), for example:

```
[hervinai-none] LoadVideoHervinai 使用文件字段：file（V3 schema）
[hervinai-none] LoadImageHervinai 使用文件字段：image（V1）
```

which reads as "LoadVideoHervinai uses file field: file (V3 schema)" and
"LoadImageHervinai uses file field: image (V1)".

If the field name shown here isn't what you expected, the core node's structure has
changed — post that log line and it can be located straight away.

To check whether the frontend patch loaded, look for this line in the console:

```
[hervinai-none] 前端补丁已加载： LoadVideoHervinai
```

which reads as "frontend patch loaded: LoadVideoHervinai".

---

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package entry point; exports `NODE_CLASS_MAPPINGS` / `NODE_DISPLAY_NAME_MAPPINGS` and registers the frontend directory via `WEB_DIRECTORY` |
| `hervinai_none_nodes.py` | Backend node implementation (the V1 / V3 dual path) |
| `web/hervinai_none.js` | Frontend patch: empty default value + audio player |
| `requirements.txt` | No extra dependencies, intentionally empty |
| `README.md` | The Chinese documentation |
| `README_EN.md` | This document |
