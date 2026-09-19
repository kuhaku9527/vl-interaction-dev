'use strict';

// i18n_device_label.js
// Runtime localization, extracted from index.html so the monolith shrinks AND
// the mapping becomes unit-testable (tests/i18n_device_label.test.js,
// tests/i18n_ui_string.test.js).
//
// Public API (attached to window.JoyI18n for non-module usage):
//   localizeDeviceLabel(label) -> string
//   DEVICE_LABEL_MAP          -> Array<[RegExp, string]>
//   localizeUiString(text)    -> string   (issue #47: key-panel UI strings)
//   UI_STRING_MAP             -> Array<[RegExp, string]>
//   applyUiI18n(root?)        -> void      (runtime pass over static markup)
//
// Maps OS-reported English device names (e.g. "OBS Virtual Camera") and
// user-facing English UI strings (e.g. "Streaming", "Camera Selection") to
// Chinese. Conservative: only known English patterns are replaced; unknown
// labels — including already-Chinese strings on a zh-locale OS — pass through
// unchanged, so we never mistranslate. Loaded via <script src> in index.html
// <head> BEFORE the inline app script that calls window.JoyI18n.*.
//
// v1.0: extracted from the inline localizeDeviceLabel() cluster (PR #56).
// v1.1: added UI_STRING_MAP / localizeUiString / applyUiI18n for issue #47.

(function () {
  // Ordered most-specific first: a more specific phrase must precede its generic
  // substring so the generic rule does not partially mangle it
  // (e.g. "Integrated Webcam" before "Webcam"; "USB* Camera" before "Camera";
  // "FaceTime HD Camera" before "HD Camera" before "Camera"; "Headset Microphone"
  // before "Headset"/"Microphone").
  const DEVICE_LABEL_MAP = [
    [/\bOBS Virtual Camera\b/i, 'OBS 虚拟摄像头'],
    [/\bVirtual Camera\b/i, '虚拟摄像头'],
    [/\bIntegrated Webcam\b/i, '内置摄像头'],
    [/\bIntegrated Camera\b/i, '内置摄像头'],
    [/\bFaceTime HD Camera\b/i, 'FaceTime 高清摄像头'],
    [/\bHD Webcam\b/i, '高清摄像头'],
    [/\bUSB[\s0-9.]*Camera\b/i, 'USB 摄像头'],
    [/\bWebcam\b/i, '摄像头'],
    [/\bHD Camera\b/i, '高清摄像头'],
    [/\bCamera\b/i, '摄像头'],
    [/\bHeadset Microphone\b/i, '耳机麦克风'],
    [/\bHeadset\b/i, '耳机'],
    [/\bMicrophone\b/i, '麦克风'],
    [/\bMic\b/i, '麦克风'],
    [/\bSpeakers\b/i, '扬声器'],
    [/\bSpeaker\b/i, '扬声器'],
    [/\bBluetooth\b/i, '蓝牙'],
    [/\bHeadphones\b/i, '耳机'],
  ];

  function localizeDeviceLabel(label) {
    if (!label) return label;
    let s = label;
    for (const [re, zh] of DEVICE_LABEL_MAP) s = s.replace(re, zh);
    return s;
  }

  // --- UI string i18n (issue #47: key-panel localization) -------------------
  // Same discipline as DEVICE_LABEL_MAP: ordered most-specific first. A longer
  // phrase that contains a shorter key MUST precede it, otherwise the generic
  // rule mangles it mid-string. Examples enforced here:
  //   "RTSP Stream URL" before "RTSP Stream"
  //   "Camera Selection" / "VLM Output on Camera View" before "Camera"
  //   "Delete failed: " / "Save failed: " before "Delete " / ": "
  //   " chunks, " before ", "
  // Conservative passthrough: unknown strings, falsy input, and already-Chinese
  // strings are returned unchanged (never mistranslated).
  const UI_STRING_MAP = [
    [/\bStart webcam capture\b/i, '开始摄像头采集'],
    [/\bStop webcam capture\b/i, '停止摄像头采集'],
    [/\bStart RTSP capture\b/i, '开始 RTSP 采集'],
    [/\bStop RTSP capture\b/i, '停止 RTSP 采集'],
    [/\bStart screen capture\b/i, '开始屏幕采集'],
    [/\bStop screen capture\b/i, '停止屏幕采集'],
    [/\bRTSP Stream URL\b/i, 'RTSP 流地址'],
    [/\bRTSP Stream\b/i, 'RTSP 流'],
    [/\bVLM Output on Camera View\b/i, '摄像头画面 VLM 输出'],
    [/\bCamera Selection\b/i, '摄像头选择'],
    // Main Content Order arrow labels: both contain the generic key below, so
    // they MUST sit above [/Camera/] or it renders as "摄像头 → VLM Output Info".
    [/\bCamera → VLM Output Info\b/i, '摄像头 → VLM 输出信息'],
    [/\bVLM Output Info → Camera\b/i, 'VLM 输出信息 → 摄像头'],
    [/\bCamera\b/i, '摄像头'],
    [/No local VLM services found\. Using NVIDIA API Catalog \(requires API key from build\.nvidia\.com\)/i, '未找到本地 VLM 服务，改用 NVIDIA API Catalog（需 build.nvidia.com 的 API Key）'],
    [/Failed to load network settings: /i, '加载网络设置失败：'],
    [/Failed to load: /i, '加载失败：'],
    [/Delete failed: /i, '删除失败：'],
    [/Save failed: /i, '保存失败：'],
    [/Sync failed: /i, '同步失败：'],
    [/Ingest failed: /i, '导入失败：'],
    [/ chunks, /i, ' 个分块，'],
    [/\bSynced /i, '已同步 '],
    [/\bIngested /i, '已导入 '],
    [/\bDeleted /i, '已删除 '],
    [/\bSyncing /i, '正在同步 '],
    [/\bIngesting /i, '正在导入 '],
    [/\bDeleting /i, '正在删除 '],
    [/\bDelete /i, '删除 '],
    [/Saving\.\.\./i, '保存中...'],
    [/Saved\. Health re-tested below\./i, '已保存。下方已重新检测健康状态。'],
    [/Probing providers\.\.\./i, '正在探测提供方...'],
    [/Probe complete \(v1 traffic stays direct per ADR-0012 §4\)\./i, '探测完成（v1 流量按 ADR-0012 §4 直连）。'],
    [/Enter a wiki\/<game> folder path first\./i, '请先输入 wiki/<游戏> 文件夹路径。'],
    [/Provide both a namespace \(game\) and markdown text\./i, '请提供命名空间（游戏）与 markdown 文本。'],
    // 2026-09-18：关于页整句（必须排在下面 `: ` 与 `, ` 两条【全局替换】规则之前，
    // 否则英文逗号/冒号会被无差别换成全角，产出「issues，roadmap」这种半吊子结果）。
    [/MIT-licensed open source\. See/i, 'MIT 许可开源。参见'],
    [/for issues, roadmap, and changelog\./i, '查看 issue、路线图与更新日志。'],
    [/: /i, '：'],
    [/, /i, '，'],
    [/ embedded/i, ' 已嵌入'],
    [/ errors/i, ' 个错误'],
    [/ rows removed/i, ' 行已移除'],
    [/\bStreaming\b/i, '直播中'],
    [/Preparing first metrics\.\.\./i, '正在准备首批指标...'],
    [/\bModel configured\b/i, '模型已配置'],
    [/\bBT listening\b/i, '蓝牙监听中'],
    [/\bASR connection failed\b/i, 'ASR 连接失败'],
    [/\bConnected\b/i, '已连接'],
    [/\bDisconnected\b/i, '未连接'],
    [/\bNo cameras found\b/i, '未找到摄像头'],
    [/Error detecting cameras/i, '检测摄像头出错'],
    [/Detecting cameras\.\.\./i, '正在检测摄像头...'],
    [/\bLLM ERR\b/i, 'LLM 错误'],
    [/\bTTS ERR\b/i, 'TTS 错误'],
    [/\bKWS ERR\b/i, 'KWS 错误'],
    [/\bVideo Source\b/i, '视频源'],
    [/\bWebcam Capture\b/i, '摄像头采集'],
    [/\bScreen Capture\b/i, '屏幕采集'],
    [/\bIdle\b/i, '空闲'],
    [/\bProcessing Interval\b/i, '处理间隔'],
    [/\bFrames per Batch\b/i, '每批帧数'],
    [/\bServices\b/i, '服务'],
    // The 6 backend "local-hint" lines (index.html 576/603/631/660/687/732) MUST
    // precede BOTH [/\bAPI Key\b/i] (consumes their "API key" tail) and
    // [/\bLocal\b/i] (consumes their "Local" head) — otherwise they come out
    // half-translated as "本地 default endpoint · no API 密钥".
    [/\bLocal default endpoint · no API key\b/i, '本地默认端点 · 无需 API 密钥'],
    [/\bLocal Whisper \/ FunASR · no API key\b/i, '本地 Whisper / FunASR · 无需 API 密钥'],
    [/\bLocal agent · no API key\b/i, '本地 agent · 无需 API 密钥'],
    [/\bLocal bge-m3 · no API key\b/i, '本地 bge-m3 · 无需 API 密钥'],
    [/\bLocal Jarvis TTS · no API key\b/i, '本地 Jarvis TTS · 无需 API 密钥'],
    [/\bAPI Base URL\b/i, 'API 基础地址'],
    [/\bAPI Key\b/i, 'API 密钥'],
    [/\bAPI URL\b/i, 'API 地址'],
    // --- Services-config panel + Settings + Memory Store (issue #47 follow-up)
    // Placed immediately above [/\bModel\b/i] because several new keys CONTAIN
    // an existing shorter key and would be half-eaten if they followed it:
    //   "Save this set" / "Save Memory Store"       contain \bSave\b
    //   "No editable Memory Store ..."              contains \bMemory Store\b
    //   "Embedding / vector provider settings ..."  contains \bEmbedding\b
    //   "Local wiki corpora ..."                    contains \bLocal\b
    //   "Six pluggable backends. Save applies ..."  contains \bSave\b
    //   "Wake / ASR"                                would hit \bASR\b if it existed
    // Every rule below is anchored with \b and matches a full key or a phrase
    // whose remainder is intentional; proper nouns (WebRTC, ADR-0012,
    // memory-store, wiki/<game>, VLM/VLM Output) are deliberately preserved.
    // 2026-09-18 修正：原为「六类可插拔后端」，但 #servicesPanel 实际只有 5 个槽位
    // （llm / summary / asr / embedding / tts）—— agent 已分离到独立的「委派」面板，
    // 它不是模型推理服务（见 services_config.py:53-56 与 doc/adr/0020 修订 R1.4）。
    [/\bSix pluggable backends\. Save applies at runtime; no service restart\./i, '五类可插拔后端；保存即时生效，无需重启服务'],
    [/\bFive pluggable backends\. Save applies at runtime; no service restart\./i, '五类可插拔后端；保存即时生效，无需重启服务'],
    // 2026-09-18 补充：设置页「说明文字」漏网条目（用户反馈「还有很多小文字没汉化」）。
    // 均为完整句子，必须排在短词规则（\bSave\b / \bTest\b / \bLocal\b 等）之前。
    [/\bScale animation when new VLM response arrives\b/i, '新回复到达时的缩放动画'],
    [/\bDrop old frames if delay exceeds this \(0 = no intervention\)/i, '延迟超阈值即丢弃旧帧（0 = 不干预）'],
    [/\bRun Qwen3\.5-122B-A10B-FP8 for delegated questions, visual reasoning, and chart tasks in the background\b/i, '后台运行 Qwen3.5-122B-A10B-FP8，处理委派问题、视觉推理与图表任务'],
    [/\bBackground frames per second relative to foreground streaming FPS\b/i, '后台帧率，相对于前台流式 FPS'],
    [/\bRecent background frame cache cap; default and maximum are 100\b/i, '后台帧缓存上限；默认与最大均为 100'],
    [/\bInclude request JSON \(image \+ prompt\) under the prompt area; collapsed by default\b/i, '在提示词区域下方显示请求 JSON（图像 + prompt）；默认折叠'],
    [/\bInclude API response JSON under the VLM output; collapsed by default\b/i, '在 VLM 输出下方显示 API 响应 JSON；默认折叠'],
    [/\bDisplay mid-term and long-term memory content below VLM output\b/i, '在 VLM 输出下方显示中期与长期记忆内容'],
    [/\bReserve only; v1 traffic stays direct \(ADR-0012 §4\)/i, '仅预留；v1 流量仍直连（ADR-0012 §4）'],
    [/\bSave this set\b/i, '保存当前组合为预设'],
    [/\bSave Memory Store\b/i, '保存记忆存储'],
    [/\bSave\b/i, '保存'],
    [/\bNo editable Memory Store fields returned by the server\./i, '服务端未返回可编辑的记忆存储字段'],
    [/\bEmbedding \/ vector provider settings\. Saved to the memory-store network config\./i, '嵌入/向量服务设置；保存到 memory-store 网络配置'],
    [/\bLocal wiki corpora \(per game\)\. Sync a wiki\/<game> folder into a vector namespace\./i, '本地知识库语料（按游戏）。把 wiki/<游戏> 目录同步为向量命名空间'],
    [/\bLocal\b/i, '本地'],
    [/\bCloud\b/i, '云端'],
    [/\bPreset name\b/i, '预设名称'],
    [/\bProvider\b/i, '服务商'],
    [/\bProbe\b/i, '探测'],
    [/\bTest\b/i, '测试'],
    [/\bDelete\b/i, '删除'],
    [/— saved presets —/i, '— 已保存的预设 —'],
    [/\bAbout\b/i, '关于'],
    [/\bSettings\b/i, '设置'],
    [/\brepository\b/i, '仓库'],
    [/\bMax Video Latency \(seconds\)/i, '最大视频延迟（秒）'],
    [/\bDrop old frames if delay exceeds this \(0 = no intervention\)/i, '延迟超阈值即丢弃旧帧（0 = 不干预）'],
    [/\bWake \/ ASR\b/i, '唤醒 / ASR'],
    [/\bScale animation when new VLM response arrives\b/i, '新回复到达时的缩放动画'],
    [/\bReserve only; v1 traffic stays direct \(ADR-0012 §4\)/i, '仅预留；v1 流量仍直连（ADR-0012 §4）'],
    [/\bIngest\b/i, '导入'],
    [/\bSync folder path \(wiki\/<game>\)/i, '同步目录路径（wiki/<游戏>）'],
    [/\bSync\b/i, '同步'],
    [/\bModel\b/i, '模型'],
    [/\bAPI Status\b/i, '接口状态'],
    [/\bMain LLM\b/i, '主 LLM'],
    [/\bSummarizer\b/i, '摘要'],
    [/\bSummary\b/i, '摘要'],
    [/\bEmbedding\b/i, '嵌入'],
    [/\bMemory Store\b/i, '记忆存储'],
    [/\bKnowledge Base\b/i, '知识库'],
    [/\bNo knowledge bases yet\./i, '暂无知识库'],
    [/\bGame \/ namespace\b/i, '游戏 / 命名空间'],
    [/Paste wiki markdown/i, '粘贴 wiki Markdown'],
    [/\bDrop first\b/i, '丢弃首段'],
    [/\bReady\b/i, '就绪'],
    [/\bLight\b/i, '浅色'],
    [/\bDark\b/i, '深色'],
    [/\bAuto\b/i, '自动'],
    [/\bPlain Text\b/i, '纯文本'],
    [/\bMid-term memory\b/i, '中期记忆'],
    [/\bLong-term memory\b/i, '长期记忆'],
    [/\bPilot \(listening\)/i, '驾驶员（监听中）'],
    [/Speaking:/i, '朗读：'],
    [/\bLayout\b/i, '布局'],
    [/\bVisual Effects\b/i, '视觉效果'],
    [/\bVisual Style\b/i, '视觉风格'],
    [/\bAudio Output\b/i, '音频输出'],
    [/\bBackground Model\b/i, '后台模型'],
    [/\bDebug\b/i, '调试'],
    [/\bNetwork Proxy\b/i, '网络代理'],
    [/\bMain Content Order\b/i, '主内容排序'],
    [/\bPop-in Animation\b/i, '弹入动画'],
    [/\bGreen Glow Effect\b/i, '绿色辉光'],
    [/\bFade Effect\b/i, '淡出效果'],
    [/\bColorful UI Accents\b/i, '彩色界面点缀'],
    [/\bSpeak VLM output\b/i, '朗读 VLM 输出'],
    [/\bEnable delegation solver\b/i, '启用量级委派求解'],
    [/\bFrame multiplier\b/i, '帧倍率'],
    [/\bMax background frames\b/i, '最大后台帧数'],
    [/\bShow request payload\b/i, '显示请求载荷'],
    [/\bShow response payload\b/i, '显示响应载荷'],
    [/\bShow memory state\b/i, '显示记忆状态'],
    [/\bEnable proxy\b/i, '启用代理'],
    [/\bProxy host\b/i, '代理主机'],
    [/\bProxy port\b/i, '代理端口'],
    [/\boptional\b/i, '可选'],
    [/\bChoose which element appears at the top\b/i, '选择置顶显示的元素'],
    [/\bShow text overlay directly on video feed\b/i, '在视频画面上直接叠加文字'],
    [/\bBorder glow on new VLM response\b/i, '新 VLM 回复时边框发光'],
    [/\bGradually fade response after 2 seconds\b/i, '2 秒后逐渐淡出回复'],
    [/\bColor-coded icons and input focus glows\b/i, '彩色图标与输入框聚焦发光'],
    [/\bPlay TTS audio for each visible response\b/i, '为每个可见回复播放 TTS 语音'],
    [/\bNone\b/i, '无'],
    [/\bAt the top\b/i, '顶部'],
    [/\bAt the bottom\b/i, '底部'],
    [/\bBT listening input device\b/i, '蓝牙监听输入设备'],
    [/Loading…/i, '加载中…'],
    [/ blocks · /i, ' 个数据块 · '],
    [/ indexed/i, ' 已索引'],
  ];

  function localizeUiString(text) {
    if (!text) return text;
    let s = text;
    for (const [re, zh] of UI_STRING_MAP) s = s.replace(re, zh);
    return s;
  }

  // Runtime localization pass for static markup.
  //   - Elements opt in with a `data-i18n` flag; their textContent is used as
  //     the lookup key (so the visible English stays the single source of truth
  //     and Chinese is resolved only from UI_STRING_MAP, never hardcoded here).
  //   - Attributes opt in with `data-i18n-title` / `data-i18n-aria` /
  //     `data-i18n-placeholder`; the attribute value is the lookup key.
  // Idempotent: a localized (Chinese) string never rematches an English key.
  function applyUiI18n(root) {
    root = root || (typeof document !== 'undefined' ? document : null);
    if (!root || typeof root.querySelectorAll !== 'function') return;
    root.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = (el.textContent || '').trim();
      if (!key) return;
      const out = localizeUiString(key);
      if (out !== key) el.textContent = out;
    });
    const attrKeys = [
      ['data-i18n-title', 'title'],
      ['data-i18n-aria', 'aria-label'],
      ['data-i18n-placeholder', 'placeholder'],
    ];
    for (const [attr, prop] of attrKeys) {
      root.querySelectorAll('[' + attr + ']').forEach((el) => {
        const key = (el.getAttribute(attr) || '').trim();
        if (!key) return;
        const out = localizeUiString(key);
        if (out !== key) el.setAttribute(prop, out);
      });
    }
  }

  // window in browser/jsdom; globalThis fallback keeps a bare-node import safe.
  const root = typeof window !== 'undefined' ? window : globalThis;
  root.JoyI18n = {
    localizeDeviceLabel,
    DEVICE_LABEL_MAP,
    localizeUiString,
    UI_STRING_MAP,
    applyUiI18n,
  };
})();
