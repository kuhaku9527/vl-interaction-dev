# 屏幕采集链路真实分辨率与画质损失（实测）

- 日期：2026-09-20
- 端点身份：**测试端点（AFK）**
- 机器：Windows 11 + RTX 5060 Ti 16GB，Chrome 153（`navigator.userAgent` 实读），主显示 2560×1440
- 方法：真实浏览器（dsh-ego-browser）实测 + 后端源码/GGUF 元数据核算
- 未启动 llama-server（GPU 全程空闲），未启动任何游戏

---

## 0. 一句话结论

**瓶颈在采集端，不在推理端。** 前端 `getDisplayMedia` 的 `ideal: 960×540` 是唯一真正生效的削减点；后端 `max_pixels=1048576` 在现行链路上**完全不生效（恒等变换）**，因为 960×540 请求实测只协商出 888×540（479,520 px），远小于 1M 预算。游戏画质提升**能**传导到 VLM 输入（1440p 实测 → 1,008 视觉 token，约当前 432 的 **2.3 倍**），且不被 `max_pixels` / `--image-max-tokens` 卡住——但**必须先把采集请求放宽**，否则游戏侧再高画质也在第一步就被压掉。

---

## 1. 削减链逐段实测/推算表

| # | 环节 | 输入 | 输出 | 依据 |
|---|------|------|------|------|
| 1 | 游戏渲染 | — | 2560×1440 = **3,686,400 px** | 例值（本机主显示实测 2560×1440） |
| 2 | `getDisplayMedia` | `ideal: 960×540` | **888×540 = 479,520 px（13.0%）** | ✅ **真实屏幕捕获实测**（见 §2 测试 A/C-low） |
| 3 | `grabFrame()` bitmap | 888×540 | **888×540 = 479,520 px** | ✅ **真实实测**（`bitmap.width/height`） |
| 4 | canvas → JPEG q0.92 | 888×540 | 尺寸不变，**有损**；960×540 基准 ≈ **67 KB** | ⚠️ 合成图实测（见 §2 测试 D） |
| 5 | `max_pixels=1048576` | 479,520 px | **479,520 px（不缩放）** | ✅ **PIL 实测 + 公式核实**（见 §3） |
| 6 | `--image-max-tokens` | — | **未配置；mmproj 无该键 → 不设每图上限** | ✅ 启动参数 + GGUF 21 键全量枚举 |
| 7 | 视觉 token | 888×540 | **432 token**（27×16） | ✅ 由 mmproj 推导并经 448 实测标定 |

### 第 7 段（token）的推导与标定

mmproj GGUF 元数据实读（`clip.*`）：

```
clip.vision.patch_size        = 16
clip.vision.spatial_merge_size = 2
clip.vision.image_size        = 768
```

⇒ 每个视觉 token 覆盖 `16 × 2 = 32 px` 边长 ⇒ **1024 px / token**。

**标定验证（唯一一次真实 token 计数）**：`ARCHITECTURE.md` §8 与 `logs/fa-probe-llama.log` 记录
`prompt eval time = 453.64 ms / 448 tokens` @ 768×576 图。

```
768×576 → (768/32) × (576/32) = 24 × 18 = 432 image tokens
448 (实测 prompt eval) − 432 = 16 tokens 文本 —— 精确吻合，模型成立
```

### 各档位视觉 token（公式 `(W/32) × (H/32)`）

| 采集规格 | 像素 | 经 `max_pixels` | 视觉 token | 相对当前 |
|---|---|---|---|---|
| 888×540（**实测**，960 ideal） | 479,520 | 不变 | **432** | 1.00× |
| 338×540（**实测**，960 ideal，竖屏源） | 182,520 | 不变 | **160** | 0.37× |
| 960×540（名义请求值） | 518,400 | 不变 | **480** | 1.11× |
| 800×450（`live_ui.js` 按钮路径） | 360,000 | 不变 | **350** | 0.81× |
| 1280×720（候选） | 921,600 | 不变 | **880** | **2.04×** |
| 1080p 1920×1080 | 2,073,600 | → 1365×768 | **1,008** | **2.33×** |
| 1440p 2560×1440 | 3,686,400 | → 1365×768 | **1,008** | **2.33×** |
| 4K 3840×2160 | 8,294,400 | → 1365×768 | **1,008** | 2.33× |

**注意**：1080p / 1440p / 4K 经 `max_pixels` 后**全部坍缩到同一个 1365×768**（见 §3），
所以「1440p 比 1080p 更清晰」**在推理端不成立**——两者 token 数完全相同。
真正的分档点是 `max_pixels`，不是游戏分辨率。

---

## 2. 浏览器实测记录

> **真实性声明**：测试 A / B / C 全部是 **`getDisplayMedia` 真实屏幕/窗口捕获**，由 Chrome 屏幕选择器授权后取得真实 `MediaStreamTrack`，`getSettings()` 与 `grabFrame()` 均为真实返回值。测试 D **是合成 canvas 测试图**（用于隔离降采样/JPEG 的数学损失，与捕获源无关），已明确标注。

### 测试 A —— 真实窗口捕获，请求 `ideal: 960×540`（= 出厂参数）★

调用参数与 `screen_capture.js` 逐字一致（`displaySurface:'window'`, `frameRate:{ideal:1}`, `width:{ideal:960}`, `height:{ideal:540}`）：

```json
"settings": { "width": 888, "height": 540, "frameRate": 1,
              "displaySurface": "window", "aspectRatio": 1.6444,
              "resizeMode": "crop-and-scale", "screenPixelRatio": 1.5,
              "deviceId": "window:196978:0" },
"constraints": { "frameRate": 1, "height": 540, "width": 960 },
"bitmap":  { "w": 888, "h": 540 },
"capabilities": { "width": { "max": 2048 }, "height": { "max": 1244 },
                  "aspectRatio": { "max": 2560 } }
```

**⇒ 关键结论：`ideal: 960` 没有得到 960。** 浏览器把 `ideal` 当作**包围盒预算**并**保持源宽高比**：
源窗口 2049×1244（aspect 1.647），浏览器锁定高度 540、宽度按比例得 `540 × 1.647 ≈ 888` —— 实测 888，吻合。

`grabFrame()` bitmap 与 `getSettings()` **完全一致（888×540）**：`canvas.width = width` 这一步**没有额外削减**。
⚠️ 任务书猜测「`bitmap.width` 可能不同于 `getSettings()`」——**实测否证**，两者相同。

### 测试 B —— 真实窗口捕获，请求 `ideal: 2560×1440`（升档验证）

```json
"settings": { "width": 1514, "height": 1394, "frameRate": 1, "screenPixelRatio": 1 },
"bitmap":  { "w": 1514, "h": 1394 },
"capabilities": { "width": { "max": 1514 }, "height": { "max": 1394 } }
```

**⇒ 升档有效。** 请求 2560×1440 得到 **1514×1394（= 该窗口原生尺寸，未降采样）**，而非 960×540。
`capabilities.max` 被源尺寸钳制到 1514×1394，说明**上限由源决定，不由 `ideal` 决定**。

### 测试 C —— 真实屏幕捕获 A/B（同一 `displaySurface:'monitor'`，仅改 `ideal`）★

| 请求 `ideal` | 实际 `getSettings()` | `grabFrame` bitmap | grab ms | encode ms | JPEG 字节 |
|---|---|---|---|---|---|
| **960×540** | 338×540（源 `screen:14:0`，aspect 0.626） | 338×540 | 36.1 | 21.8 | 46,788 |
| **2560×1440** | **2560×1440**（源 `screen:0:0`，主屏） | 2560×1440 | 22.7 | 87.3 | **376,149** |

`capabilities`（high 档）：`width.max = 2560`、`height.max = 1440` —— **完全支持 1440p 请求**。

**像素差距：338×540 = 182,520 → 2560×1440 = 3,686,400，即 20.2×。**
同为 `ideal` 语义，仅改数值，采集分辨率差 20 倍 ⇒ **`ideal: 960×540` 是主动的、大幅的下采样限制**。

（诚实标注：low 档选中了竖屏 `screen:14:0`、high 档选中主屏 `screen:0:0`，**两者不是同一块屏**，
故 20.2× 这个具体倍数含源选择混淆。但**机制**已被 A/B 与测试 B 双重确认：
`ideal` 是软预算 + 保持宽高比 + 不上采样 + 上限由源钳制。）

### 测试 D —— 降采样 / JPEG 质量损失量化 ⚠️ **合成图，非真实捕获**

源：合成 canvas 测试图 2560×1440（等宽小字 + 彩色边缘 + 灰阶渐变）。
判据：PSNR（上采样回 2560×1440 后与原生比）、文件大小。q 对应 `screen_capture.js` 的 0.92 与 `live_ui.js` 的 0.7。

| 规格 | 占原生像素 | q=0.92 PSNR | q=0.92 大小 | q=0.70 PSNR | q=0.70 大小 |
|---|---|---|---|---|---|
| 2560×1440 | 100% | **44.48 dB** | 396.4 KB | 36.33 dB | 272.9 KB |
| 1920×1080 | 56.3% | 23.91 dB | 239.2 KB | 23.81 dB | 153.5 KB |
| 1365×768 | 28.4% | 21.88 dB | 117.7 KB | 21.83 dB | 78.3 KB |
| 1280×720 | 25.0% | 21.83 dB | 108.7 KB | 21.78 dB | 69.3 KB |
| **960×540** | **14.1%** | **20.43 dB** | **67.0 KB** | 20.39 dB | 43.1 KB |
| 800×450 | 9.8% | 20.08 dB | 50.3 KB | 20.05 dB | 32.0 KB |

**⇒ 两条重要结论：**
1. **降采样主导全部损失**。2560×1440 → 960×540 损失 **24 dB**（44.48 → 20.43）；
   而同一尺寸下 JPEG q0.92 → q0.70 只损失 **0.04 dB**。
   ⇒ **分辨率是唯一有意义的杠杆，JPEG 质量因子几乎无关。**
2. `toDataURL` 耗时从 21.8ms（888×540）涨到 87.3ms（2560×1440），约 **4×**；1 fps 下占空比 8.7%，可接受。

---

## 3. `max_pixels` 的实际生效情况 —— **确认「根本没生效」**

### 3.1 公式（源码实读）

`services/webinfer/io_utils.py::_resize_image_if_needed`：

```python
width, height = image.size
if max_pixels <= 0 or width * height <= max_pixels:
    return None                      # None = 不缩放，原图直通
scale = (max_pixels / (width * height)) ** 0.5
new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
return image.resize(new_size, Image.LANCZOS)
```

即 **`scale = sqrt(max_pixels / (W·H))`，仅当 `W·H > max_pixels` 时触发；否则恒等返回。**
调用点：`infer_loop.py` → `compose_live_visual_messages(..., max_pixels=self.config.max_pixels)`
→ `prompt_assembly.py::_build_live_visual_user_message` → `io_utils._resize_frame_image_b64`。

### 3.2 PIL 实测（`max_pixels=1048576`，真实解码+缩放）

```
gdm low window       888x540 =   479,520 px -> 888x540 =   479,520 px  CHANGED=False
gdm low portrait     338x540 =   182,520 px -> 338x540 =   182,520 px  CHANGED=False
ideal 960x540        960x540 =   518,400 px -> 960x540 =   518,400 px  CHANGED=False
1440p               2560x1440 = 3,686,400 px -> 1365x768 = 1,048,320 px  CHANGED=True
1080p               1920x1080 = 2,073,600 px -> 1365x768 = 1,048,320 px  CHANGED=True
4K                  3840x2160 = 8,294,400 px -> 1365x768 = 1,048,320 px  CHANGED=True
```

### 3.3 核实结论：**推断成立 —— 上游 `max_pixels` 在现行链路上未生效**

- 出厂采集实测 **888×540 = 479,520 px**，仅为 1,048,576 预算的 **45.7%**；
- 即便按**名义**请求值 960×540 = 518,400 px，也只是预算的 **49.4%**；
- ⇒ **`_resize_image_if_needed` 恒返回 `None`，图片原样直通，`max_pixels` 是死代码路径。**

### 3.4 `max_pixels=1048576` 是多少像素？各档被缩到多少？

| 输入 | 输入像素 | `scale = sqrt(1048576/WH)` | 输出 | 输出像素 |
|---|---|---|---|---|
| 1440p 2560×1440 | 3,686,400 | 0.5333 | **1365×768** | 1,048,320 |
| 1080p 1920×1080 | 2,073,600 | 0.7111 | **1365×768** | 1,048,320 |
| 4K 3840×2160 | 8,294,400 | 0.3556 | **1365×768** | 1,048,320 |
| 960×540 | 518,400 | — | 960×540 | 518,400（不变） |

**关键洞察**：`max_pixels` 是**面积约束、且保宽高比**，所以它把一切 16:9 超限输入
**全部压到同一个 1365×768**。⇒ 1440p 与 1080p 在 VLM 眼里**没有区别**（都是 1,008 token）。
输出 1,048,320 距上限 1,048,576 只余 **256 px** —— **极其贴边的满额利用**。

对比上游 `262144`（= 512×512 等效）：1440p → scale 0.2667 → **682×384 = 261,888**（252 token）。
⇒ 本地 1048576 确实给了 **4× 视觉 token**，这个本地改动**方向正确且已生效**。

### 3.5 两条**绕过 `max_pixels`** 的旁路（实测发现，重要）

| 路径 | 是否经 `max_pixels` | 证据 |
|---|---|---|
| live-visual 路径（frames → webinfer） | ✅ 是 | `infer_loop.py` 传 `self.config.max_pixels` |
| **1 fps WS `frame` 路径**（→ `vlm_service.process_frame`） | ❌ **否** | `ws_handler.py` `elif t == "frame":` 直接 `svc.process_frame(img)`；`vlm_service.py` 用 `JOYAI_JPEG_QUALITY`(默认 92) 重新编码后直发 llama-server |
| **`/api/llm/message` 按钮路径**（`captureBtFrameB64`） | ❌ **否** | `jarvis_mode.py` 直接 `{"type":"image_url", ...data:image/jpeg;base64,...}` 发 llama-server；`server.py` 仅做 3 MB base64 丢弃 |

⇒ **若提高采集分辨率，这两条旁路会把大图无上限地送到 llama-server。**
`server.py` 的 3 MB base64 阈值（≈2.25 MB 原始 JPEG）是唯一兜底；实测 2560×1440 q0.92 = 376 KB（base64 ≈ 501 KB），**尚有 ~6× 余量，不构成约束**。

---

## 4. 判定与改进候选（**未实施**）

### 4.1 判定

| 问题 | 判定 |
|---|---|
| 提高 `ideal: 960×540` → 1440p/1080p，传到 VLM 的像素会变多吗？ | **会，且幅度很大**：432 → **1,008** token（**2.33×**）。1440p 经 `max_pixels` 后为 1,048,320 px，**未触顶**。 |
| 会被 `max_pixels` / `--image-max-tokens` 卡住吗？ | **都不会**。`max_pixels` 对 1440p 只做一次 0.533× 缩放即满足（1,048,320 < 1,048,576）；`--image-max-tokens` 未配置且 mmproj 无该键，**无每图上限**。 |
| **瓶颈在采集端还是推理端？** | **采集端（前端 `ideal: 960×540`）。** 推理端 `max_pixels` 在当前输入下是恒等变换，不是约束。 |
| 游戏开更高画质有用吗？ | **有，但必须同时放宽采集请求**；否则游戏侧 1440p 在第一步就被压到 888×540，后端拿到的东西与 720p 渲染无差别。 |
| 游戏 1440p vs 1080p 有差别吗？ | **在 VLM 输入端没有**——两者经 `max_pixels` 都坍缩到 1365×768 / 1,008 token。上限由 `max_pixels` 决定。 |

### 4.2 改进候选：采集端（**首选**）

**候选 C1（推荐）：`ideal: 960×540` → `ideal: 1280×720`**

- 改动点：`screen_capture.js` 中 `width:{ideal:960}, height:{ideal:540}`。
- 理由：1280×720 = **921,600 px < 1,048,576**，**恰好落在 `max_pixels` 预算内且不触发任何后端缩放**，
  视觉 token 432 → **880（2.04×）**，且**零后端额外开销**（无 LANCZOS 重采样）。
  这是「拿满收益、不碰上游预算」的最优单点。
- 代价（实测外推）：JPEG 约 67 KB → ~109 KB/帧（q0.92）；编码 ~22ms → ~35ms；1 fps 下带宽 ≈ 0.87 Mbps。
- 风险：低。仍在预算内，不改变 `max_pixels` 语义，不与上游分叉。

**候选 C2（激进）：→ `ideal: 1920×1080`（或 2560×1440）**

- 视觉 token **1,008（2.33×）**，但会触发后端一次 0.711×（或 0.533×）LANCZOS 缩放 —— 与 C1 相比
  **只多 128 token（+14.5%），却多一次重采样 + 5.6× 带宽**（376 KB/帧 ≈ 3 Mbps，base64 后 ≈ 4 Mbps）。
- 代价：编码 87ms/帧；WS 上行 5.6×；**并绕开 §3.5 的两条旁路**——1440p 会以全尺寸直发 llama-server。
- 风险：中。**必须同时确认旁路不被 3 MB 阈值丢弃、且不挤爆 `-c 16384`**。
  （1440p 无上限直发 = 1,008 token/帧；`LIVE_FRAME_WINDOW` 默认 6 ⇒ 单轮可达 ~6,048 token。）
- **收益/代价比明显劣于 C1**，仅在需要读极小字/OCR 时才考虑。

**候选 C3：`screen_capture.js` 的 JPEG q0.92 保持不动**
- 实测：降采样后 q0.92→0.70 仅差 **0.04 dB**。降 q 省 35% 带宽但**几乎不省画质**；
  反之若将来上高分辨率，q0.92 相对 q0.70 在原生分辨率下差 **8.15 dB**（44.48 vs 36.33）。
  ⇒ **提高分辨率时务必保持 q0.92**，两者是互补而非互斥。

**候选 C4（值得单独评估）：`live_ui.js::captureBtFrameB64` 的 `targetW = 800`、q=0.7**
- 800×450 = 360,000 px → **350 token**，且经 §3.5 旁路**不受 `max_pixels` 约束**。
- 该路径是「按按钮截图提问」的高价值场景（用户明确要模型看画面），却给了**全链路最低分辨率**。
- 建议与 C1 一并上调（如 `targetW = 1280`，q 提到 0.92）。代价同样很低。

### 4.3 改进候选：推理端（**收益存疑 / 反上游**）

**候选 I1：`max_pixels` 从 1048576 往上调 —— ❌ 不建议**

- **当前完全没必要**：§3.3 已证实它是恒等变换；只有采集端超过 1,048,576 px 时才会咬合。
  若采纳 C1（921,600 px），**依然不咬合**。
- **反上游**：上游默认 `262144`，本地已是 **4×**；再往上调会进一步偏离（`doc/research/upstream-delta-2026-09.md` 记录此为本地有意分叉）。
- 收益：仅在采集端 ≥ 1280×720 且**确实需要 >1,008 token** 时才出现。风险：每帧视觉 token 线性上涨，
  挤占 `-c 16384` 与 16GB 显存（VLM 稳态已占 9,326 MiB）。
- ⇒ **先做 C1，实测是否仍不够，再谈 I1。**

**候选 I2：`--image-min-tokens 1024` —— ⚠️ 价值可能最高，需 A/B**

- **事实（日志实证）**：`logs/fa-probe-llama.log`、`ledger-probe-llama.log`、`vram-probe-llama.log` 均含
  `W load_hparams: Qwen-VL models require at minimum 1024 image tokens to function correctly on grounding tasks`
  与 `if you encounter problems with accuracy, try adding --image-min-tokens 1024`。
- **当前状态**：`run-windows.ps1::Start-LlamaMain` 参数仅 `-m / --mmproj / --host / --port / -c / -ngl 999 / --parallel 1 / -fit off / --jinja`；
  `--image-min-tokens` **未配置**，其默认值为 `read from model`，而 **mmproj GGUF 的 21 个 KV 键中不存在该键**
  ⇒ **运行在「模型未声明」状态，故持续告警**。
- **与本次测量的关联（关键）**：模型**自称需要 ≥1024 image token 做 grounding**，
  而**出厂采集实测只给 432 token（42%）**。⇒ **采集端压缩已经把画面压到模型自述的下限之下。**
  这独立于「游戏画质」问题，是一条**未被采纳的官方建议**。
- **注意**：`--image-min-tokens` 的效果是**把小图升采样到 1024 token**，属于**推理端补偿**，
  与提高采集分辨率（拿到真实细节）**不等价**——升采样是插值，不会恢复游戏渲染的真实细节。
  ⇒ **C1 与 I2 是互补的：I2 治「token 太少」，C1 治「信息太少」。**
- **必须 A/B**：本次未启动 llama-server（GPU 让位），故**未实测其准确率影响**；`[推断]` 标记载于 §5。

---

## 5. 不确定项（诚实标注）

1. **测试 C 的 20.2× 倍数含源选择混淆**：low 档选中竖屏 `screen:14:0`、high 档选中主屏 `screen:0:0`，
   **不是同一块屏**。`ideal` 的「软预算 + 保持宽高比 + 不上采样」机制由测试 A / B / C 三重确认，
   但 20.2× 这个**具体倍数**不可直接引用；同一屏上的干净 A/B 未完成
   （后续两次尝试因屏幕选择器未能授权而失败，`window.__q2.state` 停留在 `pending`）。
2. **测试 D 为合成图**（§2 已标注）：PSNR 的**相对排序**（分辨率主导、JPEG 因子次要）稳健，
   **绝对值依赖图案**，不可外推到真实游戏帧。
3. **未启动 llama-server** ⇒ 高分辨率下的**真实 token 数与准确率均未实测**。
   表中 1,008 token 系由 GGUF（patch 16 / merge 2）推导，并用**唯一一次真实测量**（768×576 = 448）
   反向标定吻合；但未在 1440p 下做端到端复现。
4. **`--image-max-tokens` 无上限系推断**：默认值 `read from model` + mmproj 无该键
   ⇒ 推断无每图 token 上限，但**未通过发送超大图实测确认**。
5. **`--image-min-tokens 1024` 的准确率收益未经 A/B**，仅依据 llama.cpp 自身的告警文本。
6. **单轮帧数未实测**：`live_frames.py` 的 `_DEFAULT_FRAME_WINDOW = 6`（可被 `LIVE_FRAME_WINDOW` 覆盖），
   `run-windows.env` 中未见该变量 ⇒ 按默认 6 推算 §4.2-C2 的上下文压力，**未运行时验证**。
7. **3 MB base64 阈值在 4K 下的行为未测**：仅验证 1440p q0.92 ≈ 501 KB base64，余量约 6×。
8. **`--image-min-tokens` 的告警是否在每次启动必现**：仅在 3 个历史 `*-probe-llama.log` 中确认，
   未在本次（未启动服务）复现。

---

## 6. 证据（文件路径 + 关键词，不含行号）

### 实测来源

| 证据 | 位置 / 关键词 |
|---|---|
| **测试 A 真实窗口捕获** 888×540、bitmap 888×540 | 浏览器会话 `window.__gdm`（ego_js），`settings.width=888`、`screenPixelRatio=1.5`、`aspectRatio=1.6444` |
| **测试 C A/B** 338×540 vs 2560×1440 | 浏览器会话 `window.__ab.low` / `__ab.high`，`grabMs`/`encMs`/`bytes` |
| **测试 D 合成图 PSNR/大小** | 浏览器会话 `window.__q`，`nativeGrad`、`rows[].psnrVsNative` |
| **`max_pixels` PIL 实测** | 见 §3.2 输出（本次运行，`_resize_frame_image_b64` + `Image.open`） |

### 源码

| 文件 | 关键词 |
|---|---|
| `services/webui/src/joy_interaction_webui/static/screen_capture.js` | `width: { ideal: 960 }`、`height: { ideal: 540 }`、`imageCapture.grabFrame()`、`canvas.width = width`、`toDataURL('image/jpeg', 0.92)` |
| `services/webui/src/joy_interaction_webui/static/live_ui.js` | `captureBtFrameB64`、`const targetW = 800`、`toDataURL('image/jpeg', 0.7)` |
| `services/webinfer/io_utils.py` | `_resize_image_if_needed`、`scale = (max_pixels / (width * height)) ** 0.5`、`width * height <= max_pixels`、`_resize_frame_image_b64`、`_resize_data_url_if_needed` |
| `services/webinfer/adapter_types.py` | `max_pixels: int = 1048576`、`summarizer_max_pixels: int = 1048576` |
| `services/webinfer/app.py` | `--max-pixels`、`_env_int("MAX_PIXELS", 1048576)`、`_env_int("SUMMARIZER_MAX_PIXELS", 1048576)` |
| `services/webinfer/infer_loop.py` | `max_pixels=self.config.max_pixels`（live visual 分支 ×2） |
| `services/webinfer/prompt_assembly.py` | `_build_live_visual_user_message`、`_resize_frame_image_b64(frame.get("image_b64"))` |
| `services/webui/src/joy_interaction_webui/ws_handler.py` | `elif t == "frame":`、`svc.process_frame(img, frame_metadata=meta)`（**未经 max_pixels 旁路**） |
| `services/webui/src/joy_interaction_webui/vlm_service.py` | `self.jpeg_quality = int(os.getenv("JOYAI_JPEG_QUALITY", "92"))`、`image.save(..., format="JPEG", quality=self.jpeg_quality)`（**重编码后直发**） |
| `services/webui/src/joy_interaction_webui/jarvis_mode.py` | `_send_to_llm`、`"image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}`（**未经 max_pixels 旁路**） |
| `services/webui/src/joy_interaction_webui/server.py` | `image_b64 too large (%d bytes), dropped`、`3 * 1024 * 1024` |
| `services/webui/src/joy_interaction_webui/live_frames.py` | `_DEFAULT_FRAME_WINDOW: int = 6`、`frame_window_from_env` |
| `services/scripts/run-windows.ps1` | `Start-LlamaMain`、`"-m"`、`"--mmproj"`、`"-c"`、`"-ngl" "999"`、`"--parallel" "1"`、`"-fit" "off"`、`"--jinja"`（**无 `--image-max-tokens` / `--image-min-tokens`**） |
| `services/scripts/run-windows.env` | `MAIN_CTX_TOKENS=16384`（**无 `MAX_PIXELS`，故走代码默认 1048576**） |

### 日志与元数据

| 证据 | 位置 / 关键词 |
|---|---|
| **token 标定锚点** | `logs/fa-probe-llama.log`：`prompt eval time = 453.64 ms / 448 tokens`、`n_ctx_slot = 16384` |
| **未采纳的官方告警** | `logs/fa-probe-llama.log` / `logs/ledger-probe-llama.log` / `logs/vram-probe-llama.log`：`Qwen-VL models require at minimum 1024 image tokens to function correctly on grounding tasks`、`try adding --image-min-tokens 1024` |
| **mmproj 视觉几何** | `D:/AI/models/main/mmproj/mmproj-joyai-vl-interaction-preview-f16.gguf`：`clip.vision.patch_size = 16`、`clip.vision.spatial_merge_size = 2`、`clip.vision.image_size = 768`（21 个 KV 键全量枚举，**无 image_max_tokens / image_min_tokens**） |
| **llama.cpp 参数语义与默认** | `D:/AI/bin/llama.cpp/llama-server.exe --help`（b10155）：`--image-max-tokens N ... (default: read from model)`、`--image-min-tokens N ... (default: read from model)` |
| **上游 4× 差距** | `doc/research/upstream-delta-2026-09.md`：`max_pixels` 本地 `1048576` vs 上游 `262144`；`services/webinfer/live_adapter.py` 参照 |
| **448 token 与显存基线** | `ARCHITECTURE.md` §8：`768×576 图 = 448 prompt token`、`prompt eval 453.64ms (987.56 tok/s)`、`稳态显存 9,326 MiB` |

### 本次新建（只读工具，未改 `services/`）

- `scripts/_tmp_gguf_meta.py` —— 独立 GGUF 头部 KV 解析器。因已装 `gguf` 包在 numpy 2 下
  `memmap.newbyteorder` 报错，无法直接用；本脚本只读元数据、不加载权重、不起服务。

### 进程清理

- FastCtx 后台作业 `j-rdd6bp`（`node scripts/t5-static-server.mjs ... 8799`）→ **已 kill**；
  复检 `netstat`：**8799 已释放**。
- 浏览器：`stopScreenCapture()` + 所有实测 `MediaStreamTrack.stop()` 已调用，
  `getScreenCaptureStream()` 返回 `null`（无残留采集）。
- **未启动过 llama-server**：`tasklist` 复检**无 `llama-server` 进程**，GPU 全程空闲。
- **未启动任何游戏**；**未修改 `services/` 下任何文件**。
