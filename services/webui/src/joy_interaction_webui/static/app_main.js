// Extracted from index.html — t2 inline-script externalization (2026-09-19).
// 主内联脚本（原 batch-3 注释所称 script#2）：元素常量、状态、业务函数、JoyWs.register、load 引导
//
// 纯机械搬迁：本文件正文与 index.html 内联时**逐字节相同**（未 dedent、未改逻辑），
// 与 batch-3 split 的做法一致。以 classic script（非 module / 非 async / 非 defer）
// 在 <script src="./app_main.js"> 的**原位置**引入，与其余脚本共享同一全局词法环境，
// 因此顶层 const/let 仍可被后续脚本访问，相对执行顺序与搬迁前完全一致。
// 无损性由 t2 的提取脚本自证：重组后与搬迁前的 index.html 逐字节相同。

        // ASR parameters
        const ASR_TARGET_SAMPLE_RATE = 16000;
        const ASR_SILENCE_AUTO_STOP_MS = 2000;
        const ASR_SILENCE_RMS_THRESHOLD = 0.012;
        const ASR_AUDIO_BUFFER_SIZE = 4096;
        const ASR_FINAL_FALLBACK_MS = 1200;
        const ASR_WS_PATH = '/ws/asr';

        // Elements
        const themeToggle = document.getElementById('themeToggle');
        const themeIcon = document.getElementById('themeIcon');
        const themeText = document.getElementById('themeText');
        // jdLogo 已移除（2026-09-18 UI 精简：删左上角 logo 大块）
        const videoElement = document.getElementById('videoElement');
        // startBtn / stopBtn 常量已删除（2026-09-19 死代码清理）：#startBtn / #stopBtn
        // 已随旧捕获面板一并移除（v3.38 改为每源独立按钮 webcamStartBtn 等），
        // 这两个顶层常量连同下方「旧捕获 tabs」整块都是 DOM 已删、JS 仍在引用的残留。
        const connectionStatus = document.getElementById('connectionStatus');
        const resultText = document.getElementById('resultText');

        let vlmHistory = [];
        if (typeof syncVlmHistoryEmpty === "function") syncVlmHistoryEmpty();
        const currentPrompt = document.getElementById('currentPrompt');
        const metricsInline = document.getElementById('metricsInline');
        const markdownToggle = document.getElementById('markdownToggle');
        const markdownIcon = document.getElementById('markdownIcon');
        const markdownText = document.getElementById('markdownText');
        const copyButton = document.getElementById('copyButton');
        // v6-lite.23 — header MD/复制 按钮（v6-lite.22 起移到 result-header）：转发点击到内嵌实现，单一真值保持不动
        const mdToggleHeader = document.getElementById('mdToggleHeader');
        const copyBtnHeader = document.getElementById('copyBtnHeader');
        if (mdToggleHeader && markdownToggle) {
            mdToggleHeader.addEventListener('click', () => markdownToggle.click());
        }
        if (copyBtnHeader && copyButton) {
            copyBtnHeader.addEventListener('click', () => copyButton.click());
        }
        const latencyValue = document.getElementById('latencyValue');
        const avgLatencyValue = document.getElementById('avgLatencyValue');
        const countValue = document.getElementById('countValue');
        const btMicLevelValue = document.getElementById('btMicLevelValue');
        const btMicDeviceValue = document.getElementById('btMicDeviceValue');
        const btAsrLatencyValue = document.getElementById('btAsrLatencyValue');
        const btLlmLatencyValue = document.getElementById('btLlmLatencyValue');
        const btTtsLatencyValue = document.getElementById('btTtsLatencyValue');
        const btE2eLatencyValue = document.getElementById('btE2eLatencyValue');
        // 顶栏链路胶囊的 E2E 镜像值（B2 收纳）；与上面同族，供 speech_input.js 复用
        const e2ePillValue = document.getElementById('e2ePillValue');
        const promptPresetBtn = document.getElementById('promptPresetBtn');
        const promptPresetMenu = document.getElementById('promptPresetMenu');
        const promptPresetOptions = document.querySelectorAll('.prompt-preset-option');
        const promptEditor = document.getElementById('promptEditor');
        const promptEditorHome = document.querySelector('.prompt-editor-inline');
        const fullscreenPromptOverlay = document.getElementById('fullscreenPromptOverlay');
        const promptText = document.getElementById('promptText');
        const btListenBtn = document.getElementById('btListenBtn');
        const btListenPlayer = document.getElementById('btListenPlayer');
        const liveModeBtn = document.getElementById('liveModeBtn');
        const liveEnrollBtn = document.getElementById('liveEnrollBtn');
        const liveEnrollHint = document.getElementById('liveEnrollHint');
        const liveVideoBtn = document.getElementById('liveVideoBtn');
        const liveVideoSourceEl = document.getElementById('liveVideoSource');
        const liveProactiveToggle = document.getElementById('liveProactiveToggle');
        const liveVideoHint = document.getElementById('liveVideoHint');
        const liveProactiveHint = document.getElementById('liveProactiveHint');
        const speechBtn = document.getElementById('speechBtn');
        const speechButtons = [speechBtn].filter(Boolean);
        const promptSendBtn = document.getElementById('promptSendBtn');
        // Block 3 renamed the API form fields to service-scoped `svc-*` ids, so the
        // legacy `modelSelect`/`apiBaseUrl`/`apiKey` element ids no longer exist. Point
        // these closure refs at the LLM service inputs so applyApiSettings / fetchModels
        // read the live values (fixes the null addEventListener crash + undeclared refs).
        const modelSelect = document.getElementById('svc-llm-model');
        const apiBaseUrl = document.getElementById('svc-llm-api-base');
        const apiKey = document.getElementById('svc-llm-api-key');
        // refreshModelsBtn 常量已删除（2026-09-19 死代码清理）：#refreshModelsBtn 已被
        // Block 3 services-panel 重构取代（"Probe" 动作覆盖重新探测），DOM 中已无此 id。
        const processEvery = document.getElementById('processEvery');
        const framesPerBatch = document.getElementById('framesPerBatch');
        const maxLatency = document.getElementById('maxLatency');
        // mirrorBtn 常量已删除（2026-09-18）：#mirrorBtn 元素与其 click 绑定均已移除
        const quickCameraBtn = document.getElementById('quickCameraBtn');
        const quickCameraLabel = document.getElementById('quickCameraLabel');
        const ttsEnabledToggle = document.getElementById('ttsEnabledToggle');
        const ttsSpeakingLine = document.getElementById('ttsSpeakingLine');
        const ttsSpeakingText = document.getElementById('ttsSpeakingText');
        const backgroundEnabledToggle = document.getElementById('backgroundEnabledToggle');
        const backgroundFrameMultiplier = document.getElementById('backgroundFrameMultiplier');
        const backgroundMaxFrames = document.getElementById('backgroundMaxFrames');
        const asrPromotionToggle = document.getElementById('asrPromotionToggle');
        const asrModelName = document.getElementById('asrModelName');

        let peerConnection = null;
        let localStream = null;
        let btListenPeerConnection = null;
        let btListenStream = null;
        let btListening = false;
        let btListeningStarting = false;
        let liveModeActive = false;
        let liveModeStarting = false;
        let liveModePeerConnection = null;
        let liveModeStream = null;
        let _livePollTimer = null;
        // Addressee Detection Phase 1 enrollment (live 注册声音).
        let liveEnrollActive = false;
        let liveEnrollTimer = null;
        let liveEnrollCompleted = false;
        // C.B live visual (spec live-visual-cb.md §3 层 3): the live
        // panel owns a 1fps visual feed (screen via screen_capture.js OR
        // camera via an inline getUserMedia -> WS frame sender; single-select,
        // never both). Frames travel over the SAME main WS as jarvis/live
        // audio — server.py forwards `frame` to the live session ring buffer.
        let liveVideoActive = false;
        let liveVideoOwned = false;      // did the live panel start the capture?
        let liveCameraInterval = null;   // 1fps camera-frame sender
        let liveCameraStream = null;     // getUserMedia camera stream (live panel)
        let liveCameraFrameSeq = 0;      // monotonic frame_seq for camera frames
        // Proactive speak runtime state (from /api/live/status).
        let liveProactiveSupported = false;  // env gate LIVE_PROACTIVE_ENABLED
        let btMicLevelAudioContext = null;
        let btListenGainNode = null;
        let btMicGainAudioContext = null;
        let btMicLevelSource = null;
        let btMicLevelAnalyser = null;
        let btMicLevelFrame = null;
        let btMicLevelBuffer = null;
        let websocket = null;
        let serverConfigApplied = false;
        let fadeTimeout = null;
        let lastText = '';
        let lastTtsEventKey = '';
        let ttsEventSequence = 0;
        let ttsRequestToken = 0;
        let ttsWs = null;
        let ttsWsReadyPromise = null;
        let ttsAudioContext = null;
        let ttsNextPlayTime = 0;
        let ttsSources = [];
        let ttsPcmRemainder = null;
        let ttsSpeaking = false;
        let pendingTtsText = '';
        let ttsFinishTimer = null;
        let backgroundTaskEntries = new Map();
        let backgroundSummaryEntries = new Map();
        let lastReadyBackgroundTaskId = '';
        let backgroundConfig = null;
        let lastHistoryKey = null;
        let lastLoggedInferenceCount = 0;
        let pendingPromptEntry = null;
        let streamStartToken = 0;
        let revealVideoAfterMetricsToken = null;
        let revealVideoFallbackTimeout = null;
        let asrWs = null;
        let asrStream = null;
        let asrAudioContext = null;
        let asrSource = null;
        let asrProcessor = null;
        let asrSilence = null;
        let asrStarting = false;
        let asrRecording = false;
        let asrFinalText = '';
        let asrPartialText = '';
        let asrLastFinalText = '';
        let asrLastVoiceTime = 0;
        let asrSendPromptWhenFinal = false;
        let asrStopMode = 'toggle';
        let asrPointerId = null;
        let ignoreNextSpeechClick = false;
        let ignoreSpeechClickTimeout = null;
        let asrStartToken = 0;
        let asrFinalFallbackTimer = null;
        let asrStopRequested = false;
        let asrResettingSegment = false;
        let asrPromptInputResetTimer = null;
        let btLatency = {
            asrStartAt: null,
            asrMicReadyAt: null,
            asrConnectedAt: null,
            asrFirstPartialAt: null,
            asrFinalAt: null,
            sendStartAt: null,
            sendAckAt: null,
            llmReplyAt: null,
            ttsStartAt: null,
            ttsReadyAt: null,
            ttsPlayAt: null,
        };
        let mobileVoiceMode = false;
        // Multi-session: one ID per page/tab; sent in WebSocket and /offer so this tab gets only its VLM output.
        // sessionId now lives on window.sessionId (assigned below; originally a redundant local `let` mirror).
        window.sessionId = crypto.randomUUID ? crypto.randomUUID() : 'tab-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        let isAnalysisRunning = false;
        let selectedCameraId = null;
        let videoDevices = [];
        let preferredCameraFacing = 'environment';
        let rtspSessionId = 'default';  // For RTSP mode
        // Markdown toggle default is initialized on window.JoyState.markdownEnabled (see joy_state.js).
        function getCameraFacing(device, index = 0) {
            const label = (device?.label || '').toLowerCase();
            if (/front|user|face|前/.test(label)) {
                return 'user';
            }
            if (/back|rear|environment|world|后/.test(label)) {
                return 'environment';
            }
            return index === 0 ? 'user' : 'environment';
        }

        function syncQuickCameraButton() {
            const activeDevice = videoDevices.find(device => device.deviceId === selectedCameraId);
            const activeIndex = activeDevice ? videoDevices.indexOf(activeDevice) : 0;
            const facing = getCameraFacing(activeDevice, activeIndex);
            preferredCameraFacing = facing;

            // 标签语义 = 「点击后会切到的目标」（用户 2026-09-18 明确要求）：
            //   当前前置(user) → 显示「后置」；当前后置(environment) → 显示「前置」
            if (quickCameraLabel) {
                quickCameraLabel.textContent = facing === 'user' ? '前置' : '后置';
            }
            if (quickCameraBtn) {
                // 2026-09-18 修复「点了没反应」：
                // 原先 videoDevices.length < 2 时直接 disabled + opacity .45，
                // 用户只看到一个灰按钮、不知为何点不动（实测确认：单摄像头即被禁用）。
                // 现改为：始终可点，但在只有一路摄像头时点击给出明确提示，
                // 让「为什么没切换」可见，而不是静默失效。
                // 单设备时用 data-single-camera 标记，样式改为「提示态」而非「禁用态」。
                const isSingleCamera = videoDevices.length < 2;
                quickCameraBtn.disabled = false;
                quickCameraBtn.classList.toggle('is-single-camera', isSingleCamera);
                quickCameraBtn.title = isSingleCamera
                    ? '当前只检测到 1 路摄像头，无需切换'
                    : '切换前置/后置摄像头';
            }
        }

        // Build a placeholder <option> node for the camera <select> without any
        // HTML-string assignment (2026-09-19, t2 / deepsec guardrail).
        // 本函数是**本地** helper，故意不复用 vlm_history.js / vlm_render.js 的同名
        // createLucideIcon：本内联脚本位于那两个文件**之前**执行，此刻它们尚未定义。
        function buildCameraOption(label) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = label;
            return option;
        }

        // Enumerate and list available cameras
        async function enumerateCameras() {
            try {
                // Request permission first to get device labels
                const tempStream = await navigator.mediaDevices.getUserMedia({ video: true });
                tempStream.getTracks().forEach(track => track.stop());

                // Now enumerate with labels
                const devices = await navigator.mediaDevices.enumerateDevices();
                videoDevices = devices.filter(device => device.kind === 'videoinput');

                const cameraSelect = document.getElementById('cameraSelect');
                // 2026-09-19（t2，配合 deepsec 门禁）：清空改用 replaceChildren()。
                // 原写法是把空串赋给 HTML 属性，与「拼装 HTML」在纯文本正则下无法区分，
                // 必然被判为 high；replaceChildren() 不经过 HTML 解析器。
                cameraSelect.replaceChildren();

                if (videoDevices.length === 0) {
                    cameraSelect.replaceChildren(buildCameraOption(window.JoyI18n.localizeUiString('No cameras found')));
                    return;
                }

                videoDevices.forEach((device, index) => {
                    const option = document.createElement('option');
                    option.value = device.deviceId;
                    option.text = window.JoyI18n.localizeDeviceLabel(device.label) || (window.JoyI18n.localizeUiString('Camera') + (index + 1));
                    cameraSelect.appendChild(option);
                });

                // Select first camera by default
                selectedCameraId = videoDevices[0].deviceId;
                syncQuickCameraButton();
                console.log(`Found ${videoDevices.length} camera(s)`);
            } catch (err) {
                console.error('Error enumerating cameras:', err);
                document.getElementById('cameraSelect').replaceChildren(buildCameraOption(window.JoyI18n.localizeUiString('Error detecting cameras')));
            }
        }

        // Handle camera selection change (v3.38: use webcam module API)
        document.getElementById('cameraSelect').addEventListener('change', async (e) => {
            selectedCameraId = e.target.value;
            syncQuickCameraButton();
            console.log('Selected camera:', selectedCameraId);
            if (window.isWebcamCapturing && window.isWebcamCapturing()) {
                try {
                    window.stopWebcamCapture();
                    const constraints = {
                        deviceId: { exact: selectedCameraId },
                        width: { ideal: 1280 },
                        height: { ideal: 720 }
                    };
                    await window.startWebcamCapture(window.websocket, {
                        constraints,
                        sessionId: window.sessionId,
                    });
                    // 切换摄像头后同样接本地预览流到显示区
                    const camStream = window.getWebcamStream && window.getWebcamStream();
                    if (camStream && typeof videoElement !== "undefined" && videoElement) {
                        videoElement.classList.remove("mirrored");
                        videoElement.srcObject = camStream;
                    }
                } catch (err) {
                    console.error('Error switching camera:', err);
                }
            }
        });

        // Also trigger flash on blur (when dropdown closes)
        document.getElementById('cameraSelect').addEventListener('blur', () => {
            const cameraSelect = document.getElementById('cameraSelect');
            if (cameraSelect.value) {
                // Trigger flash animation
                cameraSelect.classList.add('applied');
                setTimeout(() => {
                    cameraSelect.classList.remove('applied');
                }, 600);
            }
        });

        function findCameraByFacing(facing) {
            return videoDevices.find((device, index) => getCameraFacing(device, index) === facing);
        }

        async function switchCameraByFacing(facing) {
            const cameraSelect = document.getElementById('cameraSelect');
            let targetDevice = findCameraByFacing(facing);

            if (!targetDevice && videoDevices.length > 1) {
                const currentIndex = Math.max(0, videoDevices.findIndex(device => device.deviceId === selectedCameraId));
                targetDevice = videoDevices[(currentIndex + 1) % videoDevices.length];
            }

            if (!targetDevice || targetDevice.deviceId === selectedCameraId) {
                return;
            }

            preferredCameraFacing = getCameraFacing(targetDevice, videoDevices.indexOf(targetDevice));
            selectedCameraId = targetDevice.deviceId;
            if (cameraSelect) {
                cameraSelect.value = selectedCameraId;
                cameraSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }
            syncQuickCameraButton();
        }

        quickCameraBtn?.addEventListener('click', () => {
            // 2026-09-18：单路摄像头时给出可见反馈，而不是静默无响应（用户反馈「点不动」）。
            // 只有 1 路设备时没有可切换目标，明确告知而非假装动作。
            if (videoDevices.length < 2) {
                if (quickCameraLabel) {
                    const prev = quickCameraLabel.textContent;
                    quickCameraLabel.textContent = '仅1路';
                    setTimeout(() => { quickCameraLabel.textContent = prev; }, 1600);
                }
                return;
            }
            const nextFacing = preferredCameraFacing === 'user' ? 'environment' : 'user';
            switchCameraByFacing(nextFacing);
        });

        // Tooltip positioning (for fixed position tooltips)
        document.querySelectorAll('.tooltip-wrapper').forEach(wrapper => {
            const icon = wrapper.querySelector('.tooltip-icon');
            const tooltip = wrapper.querySelector('.tooltip-text');

            if (icon && tooltip) {
                wrapper.addEventListener('mouseenter', () => {
                    const rect = icon.getBoundingClientRect();
                    tooltip.style.left = `${rect.left}px`;
                    tooltip.style.top = `${rect.bottom + 8}px`;
                });
            }
        });

        // 旧「输入源 tabs」整块已删除（2026-09-19 死代码清理）：
        // 该块遍历 `.input-source-tab`，而该 class 在 HTML 中出现 0 次 → forEach 恒遍历
        // 空集，块内引用的 #webcamControls / #rtspControls / #screenControls / #startBtn
        // 也都不存在（运行时取证：13 处死引用）。功能已由下方 v3.38 的「每源独立面板」
        // 取代（.capture-block + webcamStartBtn / rtspStartBtn / screenStartBtn）。
        // 注：styles.css 中的 `.input-source-tabs` / `.input-source-tab` 规则本轮未动
        // （CSS 不在本任务范围），已记录为后续清理项。

        // Per-source capture handlers (v3.38). Each video source has its own
        // start/stop button pair wired to its module. No global dispatcher.
        (function () {
            function setStatus(elem, text, color) {
                if (!elem) return;
                elem.textContent = text;
                if (color) elem.style.color = color; else elem.style.color = "";
            }

            // Webcam
            (function () {
                const startBtn = document.getElementById("webcamStartBtn");
                const stopBtn = document.getElementById("webcamStopBtn");
                const status = document.getElementById("webcamStatus");
                function refresh() {
                    const running = !!(window.isWebcamCapturing && window.isWebcamCapturing());
                    if (startBtn) startBtn.disabled = running;
                    if (stopBtn) stopBtn.disabled = !running;
                    setStatus(status, running ? "Streaming" : "Idle", running ? "var(--success-color, #4caf50)" : "");
                }
                if (startBtn) startBtn.addEventListener("click", async () => {
                    try {
                        const constraints = { width: { ideal: 1280 }, height: { ideal: 720 } };
                        const sel = document.getElementById("cameraSelect");
                        if (sel && sel.value) constraints.deviceId = { exact: sel.value };
                        if (!window.websocket || window.websocket.readyState !== WebSocket.OPEN) {
                            if (typeof connectWebSocket === "function") connectWebSocket();
                        }
                        await window.startWebcamCapture(window.websocket, {
                            constraints: constraints,
                            sessionId: window.sessionId,
                        });
                        // 接本地预览流到主显示区（与屏幕采集路径对齐：getScreenCaptureStream -> videoElement.srcObject，修复 OBS VC 黑屏）
                        const camStream = window.getWebcamStream && window.getWebcamStream();
                        if (camStream && typeof videoElement !== "undefined" && videoElement) {
                            videoElement.classList.remove("mirrored");
                            videoElement.srcObject = camStream;
                        }
                        setStatus(status, "Streaming", "var(--success-color, #4caf50)");
                    } catch (err) {
                        console.error("startWebcamCapture:", err);
                        setStatus(status, "Error: " + (err && err.message ? err.message : err), "var(--error-color, #f44336)");
                    }
                });
                if (stopBtn) stopBtn.addEventListener("click", () => {
                    window.stopWebcamCapture();
                    setStatus(status, "Idle");
                    refresh();
                });
                setInterval(refresh, 1000);
                refresh();
            })();

            // RTSP
            (function () {
                const startBtn = document.getElementById("rtspStartBtn");
                const stopBtn = document.getElementById("rtspStopBtn");
                const status = document.getElementById("rtspCaptureStatus");
                const urlInput = document.getElementById("rtspUrl");
                function refresh() {
                    const running = !!(window.isRtspCapturing && window.isRtspCapturing());
                    if (startBtn) startBtn.disabled = running;
                    if (stopBtn) stopBtn.disabled = !running;
                    if (urlInput) urlInput.disabled = running;
                    setStatus(status, running ? "Streaming" : "Idle", running ? "var(--success-color, #4caf50)" : "");
                }
                if (startBtn) startBtn.addEventListener("click", async () => {
                    const url = urlInput ? urlInput.value.trim() : "";
                    if (!url) { alert("Please enter an RTSP URL"); return; }
                    try {
                        if (!window.websocket || window.websocket.readyState !== WebSocket.OPEN) {
                            if (typeof connectWebSocket === "function") connectWebSocket();
                        }
                        await window.startRtspCapture(window.websocket, {
                            rtspUrl: url,
                            sessionId: window.sessionId,
                        });
                        setStatus(status, "Streaming", "var(--success-color, #4caf50)");
                    } catch (err) {
                        console.error("startRtspCapture:", err);
                        setStatus(status, "Error: " + (err && err.message ? err.message : err), "var(--error-color, #f44336)");
                    }
                });
                if (stopBtn) stopBtn.addEventListener("click", () => {
                    window.stopRtspCapture();
                    setStatus(status, "Idle");
                    refresh();
                });
                setInterval(refresh, 1000);
                refresh();
            })();

            // Screen Capture
            (function () {
                const startBtn = document.getElementById("screenStartBtn");
                const stopBtn = document.getElementById("screenStopBtn");
                const status = document.getElementById("screenStatus");
                function refresh() {
                    const running = !!(window.isScreenCapturing && window.isScreenCapturing());
                    if (startBtn) startBtn.disabled = running;
                    if (stopBtn) stopBtn.disabled = !running;
                    setStatus(status, running ? "Capturing (1 fps)" : "Idle", running ? "var(--success-color, #4caf50)" : "");
                }
                if (startBtn) startBtn.addEventListener("click", async () => {
                    try {
                        if (!window.websocket || window.websocket.readyState !== WebSocket.OPEN) {
                            if (typeof connectWebSocket === "function") connectWebSocket();
                        }
                        await window.startScreenCapture(window.websocket, { fps: 1 });
                        const previewStream = window.getScreenCaptureStream && window.getScreenCaptureStream();
                        if (previewStream && typeof videoElement !== "undefined" && videoElement) {
                            videoElement.classList.remove("mirrored");
                            videoElement.srcObject = previewStream;
                        }
                        setStatus(status, "Capturing (1 fps)", "var(--success-color, #4caf50)");
                    } catch (err) {
                        console.error("startScreenCapture:", err);
                        setStatus(status, "Error: " + (err && err.message ? err.message : err), "var(--error-color, #f44336)");
                    }
                });
                if (stopBtn) stopBtn.addEventListener("click", () => {
                    window.stopScreenCapture();
                    setStatus(status, "Idle");
                    refresh();
                });
                setInterval(refresh, 1000);
                refresh();
            })();
        })();

        // Services panel (v3.38): load / save / probe 6 API slots (LLM / Summary / TTS / ASR / Agent / Embedding)
        // (function bodies extracted to config_services.js → window.JoyConfig)
        (function () {
            const { readForm, writeForm, load, probe, save, wireSummaryProvider, wireSegProvider, wireServiceRowCollapse, wireModelFetch, testSlot, wireTtsControls } = window.JoyConfig;
            const saveBtn = document.getElementById("svcSaveBtn");
            const probeBtn = document.getElementById("svcProbeBtn");
            // N9: 各槽位「Test」按钮（表单当前值 → POST /api/services/test）。
            // testSlot 按 svc-{slot}-test-btn / -label / -status 取元素；summary 沿用旧 id。
            ['summary', 'llm', 'tts', 'asr', 'agent', 'embedding'].forEach(function (slot) {
                const btn = document.getElementById('svc-' + slot + '-test-btn');
                if (btn && testSlot) {
                    btn.addEventListener('click', function () {
                        testSlot(slot, readForm()[slot]);
                    });
                }
            });
            if (saveBtn) saveBtn.addEventListener("click", save);
            if (probeBtn) probeBtn.addEventListener("click", probe);
            // N8: summary provider 下拉联动默认值
            if (wireSummaryProvider) wireSummaryProvider();
            // Axis 4 (v6-lite.12): local/cloud seg + Provider named presets
            if (wireSegProvider) wireSegProvider();
            // 2026-09-18（用户要求 B）：service-row 展开/折叠（LLM+Summary 默认展开）
            if (wireServiceRowCollapse) wireServiceRowCollapse();
            // 2026-09-18：模型字段的「获取模型」按钮（上游 /models 探测）
            if (wireModelFetch) wireModelFetch();
            // 2026-09-19（用户拍板）：TTS 卡片的 provider / 音色 / 语速 / 音调 / 试听。
            // 首上免费方案（Edge TTS，无需 Key）便于测试；大厂 API 后续按需接入。
            if (wireTtsControls) wireTtsControls();
            load();
        })();

        // Memory Store sub-area (embedding/provider portion of /v1/settings/network).
        // Load is triggered when the settings modal opens (see settingsBtn handler below);
        // save is handled by joy_ws.js so the logic stays co-located with the WS/API cluster.
        (function () {
            const msSaveBtn = document.getElementById("memoryStoreSaveBtn");
            if (msSaveBtn && window.JoyWs && window.JoyWs.saveMemoryStoreSettings) {
                msSaveBtn.addEventListener("click", function () {
                    window.JoyWs.saveMemoryStoreSettings();
                });
            }
        })();

        // Radio Silence (radio-silence.md §3/§7): init 组合键监听 + 角标 +
        // 初始状态读取。面板在 settingsModal 内；打开 modal 时刷新（见 settingsBtn handler）。
        (function () {
            if (window.JoyRadioSilence && window.JoyRadioSilence.init) {
                window.JoyRadioSilence.init();
            }
        })();

        // Clear RTSP status message when user types
        const rtspUrlInput = document.getElementById('rtspUrl');
        rtspUrlInput.addEventListener('input', () => {
            const statusDiv = document.getElementById('rtspStatus');
            statusDiv.style.display = 'none';
        });

        // Green flash animation when RTSP URL is set (same as API Base URL)
        rtspUrlInput.addEventListener('blur', () => {
            if (rtspUrlInput.value.trim()) {
                rtspUrlInput.classList.add('applied');
                setTimeout(() => {
                    rtspUrlInput.classList.remove('applied');
                }, 600);
            }
        });

        // Test RTSP connection
        document.getElementById('testRtspBtn').addEventListener('click', async () => {
            const rtspUrl = document.getElementById('rtspUrl').value.trim();
            const statusDiv = document.getElementById('rtspStatus');
            const testBtn = document.getElementById('testRtspBtn');

            if (!rtspUrl) {
                statusDiv.textContent = '⚠️ Please enter an RTSP URL';
                statusDiv.style.display = 'block';
                statusDiv.style.color = '#ff6b35';
                return;
            }

            testBtn.disabled = true;
            statusDiv.textContent = '🔄 Testing connection...';
            statusDiv.style.display = 'block';
            statusDiv.style.color = '#76b900';

            try {
                const response = await fetch('/api/rtsp/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        rtsp_url: rtspUrl,
                        session_id: 'test-' + Date.now()
                    })
                });

                const data = await response.json();

                if (response.ok) {
                    const info = data.stream_info;
                    // 2026-09-19（t2）：原先把 info.codec/width/height/fps 插进 HTML 字符串。
                    // info 来自 /api/rtsp/start 的响应体（外部数据），插值进 HTML 解析器是
                    // 真实汇聚点。改为逐节点拼装：文本一律走 textContent，换行用 <br> 元素。
                    // 顺带把 ✅ 与后续文本之间的空格补回（原模板里 <br> 后无空格，视觉一致）。
                    const codecSpan = document.createElement('span');
                    codecSpan.textContent = `✅ Connected!`;
                    const brEl = document.createElement('br');
                    const detailSpan = document.createElement('span');
                    detailSpan.textContent = `${info.codec} ${info.width}x${info.height} @${info.fps}fps`;
                    statusDiv.replaceChildren(codecSpan, brEl, detailSpan);
                    statusDiv.style.color = '#76b900';

                    // Stop the test connection after 2 seconds
                    setTimeout(async () => {
                        await fetch('/api/rtsp/stop', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ session_id: data.session_id })
                        });
                    }, 2000);
                } else {
                    statusDiv.textContent = '❌ ' + (data.error || 'Connection failed');
                    statusDiv.style.color = '#ff6b35';
                }
            } catch (err) {
                console.error('RTSP test error:', err);
                statusDiv.textContent = '❌ Connection failed: ' + err.message;
                statusDiv.style.color = '#ff6b35';
            } finally {
                testBtn.disabled = false;
            }
        });

        // Tooltip positioning - dynamically position tooltip to avoid clipping
        document.querySelectorAll('.tooltip-wrapper').forEach(wrapper => {
            const icon = wrapper.querySelector('.tooltip-icon');
            const tooltip = wrapper.querySelector('.tooltip-text');

            if (icon && tooltip) {
                icon.addEventListener('mouseenter', () => {
                    const rect = icon.getBoundingClientRect();
                    const tooltipWidth = 380; // Match CSS width

                    // Position below the icon
                    let left = rect.left - 10; // Slight offset to left
                    let top = rect.bottom + 8;

                    // Ensure tooltip doesn't go off right edge of viewport
                    if (left + tooltipWidth > window.innerWidth - 20) {
                        left = window.innerWidth - tooltipWidth - 20;
                    }

                    // Ensure tooltip doesn't go off left edge
                    if (left < 20) {
                        left = 20;
                    }

                    tooltip.style.left = left + 'px';
                    tooltip.style.top = top + 'px';
                });
            }
        });
        // Build the theme indicator icon node without any HTML-string assignment
        // (2026-09-19, t2 / deepsec guardrail). Lucide 随后由下面的 createIcons()
        // 把 <i data-lucide> 替换成真实 <svg>，与原先路径一致。
        // 同样刻意不复用 vlm_history.js 的 createLucideIcon（执行顺序在本文件之后）。
        function buildThemeIcon(name) {
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', name);
            return icon;
        }

        // Enumerate cameras on page load
        enumerateCameras();
        // Theme management: Honor OS preference with manual override support
        function applyTheme(theme) {
            // theme can be 'light', 'dark', or 'auto'
            if (theme === 'light') {
                document.body.classList.add('light-theme');
                themeIcon.replaceChildren(buildThemeIcon('sun'));
                themeText.textContent = window.JoyI18n.localizeUiString('Light');
            } else if (theme === 'dark') {
                document.body.classList.remove('light-theme');
                themeIcon.replaceChildren(buildThemeIcon('moon'));
                themeText.textContent = window.JoyI18n.localizeUiString('Dark');
            } else { // 'auto' - follow OS preference
                const prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
                if (prefersLight) {
                    document.body.classList.add('light-theme');
                } else {
                    document.body.classList.remove('light-theme');
                }
                // Always show monitor icon for AUTO mode
                themeIcon.replaceChildren(buildThemeIcon('monitor'));
                themeText.textContent = window.JoyI18n.localizeUiString('Auto');
            }
            lucide.createIcons();

        }

        // Theme Toggle: cycles through Auto -> Light -> Dark -> Auto
        // （原 #jdLogo 点击/回车重载页面的绑定已随 logo 块一并移除，2026-09-18）

        themeToggle.addEventListener('click', () => {
            const currentTheme = localStorage.getItem('theme') || 'auto';
            let nextTheme;

            if (currentTheme === 'auto' || !currentTheme) {
                nextTheme = 'light';
            } else if (currentTheme === 'light') {
                nextTheme = 'dark';
            } else { // dark
                nextTheme = 'auto';
            }

            // Only save manual overrides (light/dark) to localStorage
            // Don't save 'auto' - always check OS preference when in auto mode
            if (nextTheme === 'auto') {
                localStorage.removeItem('theme');
            } else {
                localStorage.setItem('theme', nextTheme);
            }
            applyTheme(nextTheme);
        });

        // Load saved theme or detect OS preference
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'light' || savedTheme === 'dark') {
            // User has manually set a preference - use it
            applyTheme(savedTheme);
        } else {
            // No manual preference saved - default to dark (sample preview is dark-themed; user can toggle to Light/Auto)
            applyTheme('dark');
        }

        // Listen for OS preference changes when in 'auto' mode
        const colorSchemeQuery = window.matchMedia('(prefers-color-scheme: light)');
        colorSchemeQuery.addEventListener('change', (e) => {
            const currentTheme = localStorage.getItem('theme');
            // Only update if user hasn't manually set a preference
            if (!currentTheme || currentTheme === 'auto') {
                applyTheme('auto');
            }
        });

        // Load saved colorful UI accents setting (default: disabled for clean two-tone system)
        const colorfulFocusToggle = document.getElementById('colorfulFocusToggle');
        const colorfulFocusSaved = localStorage.getItem('colorfulFocus');
        if (colorfulFocusSaved === 'true') {
            document.body.classList.add('colorful-focus');
            colorfulFocusToggle.checked = true;
        } else {
            // Default: neutral white/gray icons and focus glows with red flash
            document.body.classList.remove('colorful-focus');
            colorfulFocusToggle.checked = false;
        }

        // Settings Modal
        const settingsBtn = document.getElementById('settingsBtn');
        const settingsModal = document.getElementById('settingsModal');
        const settingsClose = document.getElementById('settingsClose');
        const popInToggle = document.getElementById('popInToggle');
        const glowToggle = document.getElementById('glowToggle');
        const fadeToggle = document.getElementById('fadeToggle');
        const overlayPosition = document.getElementById('overlayPosition');
        const layoutOrder = document.getElementById('layoutOrder');
        const videoOverlay = document.getElementById('videoOverlay');

        // Settings state
        const settings = {
            popIn: localStorage.getItem('popIn') !== 'false',
            glow: localStorage.getItem('glow') !== 'false',
            fade: localStorage.getItem('fade') !== 'false',
            overlayPosition: localStorage.getItem('overlayPosition') || 'none',
            layoutOrder: localStorage.getItem('layoutOrder') || 'vlm-first',
            ttsEnabled: localStorage.getItem('ttsEnabled') !== 'false',
            // Server-driven: authoritative value arrives via server_config / asr_promotion_updated.
            asrPromotionEnabled: false
        };

        // Load saved settings
        popInToggle.checked = settings.popIn;
        glowToggle.checked = settings.glow;
        fadeToggle.checked = settings.fade;
        overlayPosition.value = settings.overlayPosition;
        layoutOrder.value = settings.layoutOrder;
        if (ttsEnabledToggle) {
            ttsEnabledToggle.checked = settings.ttsEnabled;
        }

        // Apply overlay position
        function applyOverlayPosition(position) {
            videoOverlay.classList.remove('show', 'top', 'bottom');
            const resultText = document.getElementById('resultText');
            const hasDisplayText = (vlmHistory[vlmHistory.length - 1]?.response || getVlmDisplayText(lastText)).trim() !== '';
            const hasJarvisDialog = hasJarvisDialogHistory();

            if (position !== 'none') {
                // Overlay enabled - show on video, hide response balloon (keep prompt visible)
                if (hasDisplayText) {
                    videoOverlay.classList.add('show', position);
                }
                resultText.style.display = hasJarvisDialog ? 'flex' : 'none';
            } else {
                // Overlay disabled - hide from video, show response balloon
                resultText.style.display = hasDisplayText ? 'flex' : 'none';
            }
        }
        // P0-1 (audit 2026-08-13): getVlmDisplayText lives in the post-main
        // vlm_render.js script, which loads after this inline script — an
        // immediate call here would throw ReferenceError (getVlmDisplayText is
        // not defined) and abort the whole inline script (WS bootstrap, settings
        // modal, WrappedWS IIFE, ...). Defer the overlay initial paint until that
        // script is guaranteed to have loaded. DOMContentLoaded fires after all
        // synchronous classic scripts at the end of <body> have executed.
        if (typeof getVlmDisplayText === 'function') {
            applyOverlayPosition(settings.overlayPosition);
        } else {
            window.addEventListener('DOMContentLoaded', () => {
                applyOverlayPosition(settings.overlayPosition);
            });
        }

        // Apply layout order
        function isMobileLayout() {
            return window.matchMedia('(max-width: 768px)').matches;
        }

        function isPressToTalkLayout() {
            return isMobileLayout() && mobileVoiceMode;
        }

        function applyLayoutOrder(order) {
            const mainContent = document.querySelector('.main-content');
            const videoCard = document.querySelector('.video-card');
            const resultCard = document.getElementById('vlmOutputCard');

            if (!mainContent || !videoCard || !resultCard) {
                return;
            }

            if (isMobileLayout()) {
                mainContent.insertBefore(videoCard, resultCard);
            } else if (order === 'vlm-first') {
                // VLM Output Info at top, Camera below (default)
                mainContent.insertBefore(resultCard, videoCard);
            } else {
                // Camera at top, VLM Output Info below
                mainContent.insertBefore(videoCard, resultCard);
            }
        }
        applyLayoutOrder(settings.layoutOrder);

        window.matchMedia('(max-width: 768px)').addEventListener('change', () => {
            applyLayoutOrder(settings.layoutOrder);
            updatePromptAvailability();
            syncSpeechButtons();
        });

        // Open settings modal
        settingsBtn.addEventListener('click', () => {
            settingsModal.classList.add('show');
            if (window.JoyWiki) {
                window.JoyWiki.loadHealth();
                window.JoyWiki.loadNetwork();
            }
            // Pull the latest Memory Store (embedding/provider) settings when the
            // modal opens so the form always reflects the server snapshot.
            if (window.JoyWs && window.JoyWs.loadMemoryStoreSettings) {
                window.JoyWs.loadMemoryStoreSettings();
            }
            // Radio Silence: 打开 modal 时刷新静默设置 + 状态角标。
            if (window.JoyRadioSilence && window.JoyRadioSilence.load) {
                window.JoyRadioSilence.load();
            }
        });

        // Close settings modal
        settingsClose.addEventListener('click', () => {
            settingsModal.classList.remove('show');
        });

        // Close modal when clicking outside
        settingsModal.addEventListener('click', (e) => {
            if (e.target === settingsModal) {
                settingsModal.classList.remove('show');
            }
        });

        // Handle settings changes
        popInToggle.addEventListener('change', (e) => {
            settings.popIn = e.target.checked;
            localStorage.setItem('popIn', settings.popIn);
        });

        glowToggle.addEventListener('change', (e) => {
            settings.glow = e.target.checked;
            localStorage.setItem('glow', settings.glow);
        });

        fadeToggle.addEventListener('change', (e) => {
            settings.fade = e.target.checked;
            localStorage.setItem('fade', settings.fade);
        });

        if (ttsEnabledToggle) {
            ttsEnabledToggle.addEventListener('change', (e) => {
                settings.ttsEnabled = e.target.checked;
                localStorage.setItem('ttsEnabled', settings.ttsEnabled);
                if (!settings.ttsEnabled) {
                    closeTtsWebSocket();
                } else {
                    try {
                        ensureTtsAudioContext();
                    } catch (error) {
                        console.warn('Unable to initialize TTS audio context:', error);
                    }
                }
            });
        }

        colorfulFocusToggle.addEventListener('change', (e) => {
            const enabled = e.target.checked;
            localStorage.setItem('colorfulFocus', enabled);
            if (enabled) {
                document.body.classList.add('colorful-focus');
            } else {
                document.body.classList.remove('colorful-focus');
            }
            // Force icon re-render to apply new colors
            lucide.createIcons();
        });

        overlayPosition.addEventListener('change', (e) => {
            settings.overlayPosition = e.target.value;
            localStorage.setItem('overlayPosition', settings.overlayPosition);
            applyOverlayPosition(settings.overlayPosition);
        });

        layoutOrder.addEventListener('change', (e) => {
            settings.layoutOrder = e.target.value;
            localStorage.setItem('layoutOrder', settings.layoutOrder);
            applyLayoutOrder(settings.layoutOrder);
        });

        // Panel Toggle
        function togglePanel(panelId) {
            const sidebar = document.getElementById('sidebar');
            const content = document.getElementById(panelId);
            const toggle = document.getElementById(panelId + 'Toggle');
            const sidebarPanelIds = ['vlmConfig', 'cameraConfig'];
            const isSidebarPanel = sidebarPanelIds.includes(panelId);

            if (isSidebarPanel && sidebar?.classList.contains('docked')) {
                sidebar.classList.remove('docked');
                sidebarPanelIds.forEach(id => {
                    const panelContent = document.getElementById(id);
                    const panelToggle = document.getElementById(id + 'Toggle');
                    const isActive = id === panelId;
                    panelContent?.classList.toggle('collapsed', !isActive);
                    panelToggle?.classList.toggle('collapsed', !isActive);
                });
                return;
            }

            content?.classList.toggle('collapsed');
            toggle?.classList.toggle('collapsed');

            // [Local Wiki] F4: lazy-load namespace list when the panel opens
            if (panelId === 'knowledgeBase' && window.JoyWiki) {
                window.JoyWiki.loadNamespaces();
            }

            const allSidebarPanelsCollapsed = sidebarPanelIds
                .every(id => document.getElementById(id)?.classList.contains('collapsed'));
            if (isSidebarPanel && allSidebarPanelsCollapsed) {
                sidebar?.classList.add('docked');
            }
        }


        // Fullscreen Toggle
        function toggleFullscreen() {
            const videoCard = document.getElementById('videoCard');
            const fullscreenIcon = document.getElementById('fullscreenIcon');

            videoCard.classList.toggle('fullscreen');

            // Update icon: maximize when normal, minimize when fullscreen
            if (videoCard.classList.contains('fullscreen')) {
                fullscreenIcon.setAttribute('data-lucide', 'minimize');
                if (fullscreenPromptOverlay && promptEditor) {
                    fullscreenPromptOverlay.appendChild(promptEditor);
                }
                // Sync current VLM output to fullscreen overlay
                syncVlmToFullscreen();
            } else {
                fullscreenIcon.setAttribute('data-lucide', 'maximize');
                restorePromptEditorHome();
            }
            lucide.createIcons();
        }

        function restorePromptEditorHome() {
            if (promptEditorHome && promptEditor && promptEditor.parentElement !== promptEditorHome) {
                promptEditorHome.appendChild(promptEditor);
            }
        }

        // Sync VLM output to fullscreen overlay
        function syncVlmToFullscreen() {
            const fullscreenVlmContent = document.getElementById('fullscreenVlmContent');
            const fullscreenVlmMetrics = document.getElementById('fullscreenVlmMetrics');

            if (fullscreenVlmContent) {
                fullscreenVlmContent.textContent = getFullscreenVlmText();
            }

            // 2026-09-19：没有字幕内容时**整块隐藏**（连同外框）。
            // 原实现的外框背景+边框恒常显示，空内容时就是一个写着占位文案的空盒子
            // —— 用户反馈"影响观感"。改为 is-empty 开关，由 CSS 收起整个浮层。
            const overlay = document.getElementById('fullscreenVlmOverlay');
            if (overlay) {
                const hasContent = Boolean(fullscreenVlmContent && fullscreenVlmContent.textContent.trim());
                overlay.classList.toggle('is-empty', !hasContent);
            }

            // Add metrics if available
            const latency = document.getElementById('latencyValue')?.textContent;
            const count = document.getElementById('countValue')?.textContent;
            if (latency && count) {
                // 2026-09-19（t2）：原先用模板字符串把 latency / count 插进 HTML。
                // 两者取自 DOM 文本（上游又是模型/后端派生的数值），一律改 textContent 拼装，
                // 让 HTML 解析器不再接触运行时字符串。结构与原来等价：两个 <span> 并列。
                const latencySpan = document.createElement('span');
                latencySpan.textContent = `Latency: ${latency}ms`;
                const countSpan = document.createElement('span');
                countSpan.textContent = `Count: ${count}`;
                fullscreenVlmMetrics.replaceChildren(latencySpan, countSpan);
            }
        }

        function normalizeFullscreenLine(text) {
            return String(text || '').replace(/\s+/g, ' ').trim();
        }

        function getFullscreenVlmText() {
            // 2026-09-19（用户拍板 2(c)）：全屏字幕**只显示 AI 的回复内容**，
            // 参考 GPT Live / 手机端 AI Agent 的视频聊天模式。
            //  • 不再推「输入：…」——那是聊天历史的语义，不是字幕
            //  • 回复统一过 getVlmDisplayText()（剥离 </response> 等决策 token；
            //    模型选 </silence> 时它返回 ''，于是该轮不出字幕，符合预期）
            //  • 无内容时返回 ''（占位文案「就绪」已按用户要求删除）
            const lines = [];

            vlmHistory.forEach(entry => {
                if (entry.kind === 'background' || entry.contextExcluded) {
                    return;
                }
                const response = normalizeFullscreenLine(getVlmDisplayText(entry.response));
                if (response) {
                    lines.push(response);
                }
            });

            // 尚无历史条目时，用当前这条实时输出兜底
            if (!lines.length) {
                const liveResponse = normalizeFullscreenLine(getVlmDisplayText(lastText));
                if (liveResponse) {
                    lines.push(liveResponse);
                }
            }

            return lines.slice(-4).join('\n');
        }

        // ESC key to exit fullscreen
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const videoCard = document.getElementById('videoCard');
                if (videoCard.classList.contains('fullscreen')) {
                    toggleFullscreen();
                }
            }
        });

        // toggleApiKeyField() / checkApiKeyRequirement() 已彻底删除（2026-09-19 死代码清理）。
        //
        // 原状：两者都取 #apiKeyField / #apiKeyToggle，而这两个 id 在 DOM 中都不存在
        // （旧 API 设置面板已随 2026-09-18「API Status 面板删除」一并移除）：
        //   · toggleApiKeyField() 无守卫，若被调用会直接 TypeError（当时无调用者，故未爆）；
        //   · checkApiKeyRequirement() 内部有 `if (apiKeyField && apiKeyToggle)` 守卫，
        //     但两个变量恒为 null → 整段"远端 URL 显示 Key 输入框"的逻辑永久失效，
        //     而它仍被 4 处调用（ws_dispatcher.js:196、index.html 3 处）→ 典型的**静默失效**。
        //
        // 判据「有现代等价物则修复、无则删除」：这里**无等价物可修**——新版 services 面板的
        // svc-llm-api-key / svc-summary-api-key / svc-embedding-api-key 字段是恒显的
        // （仅靠 [data-local-hide] 属性做局部隐藏），不存在"折叠态/展开态"可切换的旧对象。
        // 故连同 4 个调用点一并删除，而不是补回 DOM（遵守"用户删的元素不要恢复"硬约束）。

        // Status Update
        function updateStatus(message, state) {
            connectionStatus.textContent = message;
            connectionStatus.className = `status-badge ${state}`;
        }

        function updatePromptAvailability() {
            const canSendPrompt = true;
            promptSendBtn.disabled = false;
            speechButtons.forEach(button => {
                button.disabled = false;
            });
            promptText.placeholder = '和 BT-7274 对话...';
            promptSendBtn.title = '发送给 BT-7274';
            syncSpeechButtons();

            if (processEvery) {
                processEvery.disabled = isAnalysisRunning;
                processEvery.title = isAnalysisRunning ? '推理中不可修改，请先停止视频' : '';
            }
            if (framesPerBatch) {
                framesPerBatch.disabled = isAnalysisRunning;
                framesPerBatch.title = isAnalysisRunning ? '推理中不可修改，请先停止视频' : '';
            }
        }

        function setVideoWaitingForStream(waiting) {
            const videoCard = document.getElementById('videoCard');
            videoCard?.classList.toggle('waiting-for-stream', waiting);
        }

        function resetVideoButtons() {
            // v3.38: red Start / small Stop removed. Each capture source refreshes its own button.
        }

        function cancelRevealVideoFallback() {
            if (revealVideoFallbackTimeout) {
                clearTimeout(revealVideoFallbackTimeout);
                revealVideoFallbackTimeout = null;
            }
        }

        function revealVideoWhenReady(token) {
            if (token !== streamStartToken) {
                return;
            }
            cancelRevealVideoFallback();
            revealVideoAfterMetricsToken = null;
            setVideoWaitingForStream(false);
            updateStatus(window.JoyI18n.localizeUiString('Streaming'), 'connected');
        }

        function showProcessedVideoStream(stream, token) {
            if (token !== streamStartToken) {
                return;
            }
            videoElement.srcObject = stream;

            const markStreaming = () => {
                if (token !== streamStartToken || videoElement.srcObject !== stream) {
                    return;
                }
                isAnalysisRunning = true;
                updatePromptAvailability();
                if (revealVideoAfterMetricsToken === token) {
                    updateStatus(window.JoyI18n.localizeUiString('Preparing first metrics...'), 'processing');
                    cancelRevealVideoFallback();
                    revealVideoFallbackTimeout = setTimeout(() => {
                        revealVideoWhenReady(token);
                    }, 5000);
                } else {
                    revealVideoWhenReady(token);
                }
            };

            const playPromise = videoElement.play();
            if (playPromise && typeof playPromise.then === 'function') {
                playPromise.then(markStreaming).catch(err => {
                    console.error('Error playing video:', err);
                    markStreaming();
                });
            } else {
                markStreaming();
            }
        }

        // Detect Local VLM Services
        async function detectServices() {
            try {
                const response = await fetch('/detect-services');
                const data = await response.json();

                if (data.default) {
                    const service = data.default;
                    console.log('Detected service:', service.name);

                    // Update API Base URL
                    apiBaseUrl.value = service.url;

                    // Update hint text (svc-llm-api-base has no hint sibling after
                    // Block 3's form refactor, so guard against a null element).
                    const hintDiv = apiBaseUrl.nextElementSibling;
                    if (hintDiv) {
                        if (data.detected.length > 1) {
                            const serviceNames = data.detected.map(s => s.name).join(', ');
                            hintDiv.textContent = `Detected: ${serviceNames}`;
                        } else if (service.name === 'NVIDIA API Catalog') {
                            hintDiv.textContent = window.JoyI18n.localizeUiString('No local VLM services found. Using NVIDIA API Catalog (requires API key from build.nvidia.com)');
                        }
                    }
                }
            } catch (error) {
                console.error('Error detecting services:', error);
                // 原先在此调用 checkApiKeyRequirement('https://') 兜底展开 Key 输入框；
                // 该函数已删除（2026-09-19）——新版 services 面板的 svc-*-api-key 恒显，无需兜底。
            }
        }

        // Fetch Models
        function isValidModelName(model) {
            const value = String(model || '').trim();
            return Boolean(value) && !['undefined', 'null', 'none'].includes(value.toLowerCase());
        }

        async function fetchModels() {
            try {
                if (!apiBaseUrl || !apiKey || !modelSelect) return;

                // Get current API settings from UI
                const currentApiBase = apiBaseUrl.value.trim();
                const currentApiKey = apiKey.value.trim();

                // Build query params
                const params = new URLSearchParams();
                if (currentApiBase) {
                    params.append('api_base', currentApiBase);
                }
                if (currentApiKey) {
                    params.append('api_key', currentApiKey);
                }

                const url = `/models${params.toString() ? '?' + params.toString() : ''}`;
                const response = await fetch(url);
                const data = await response.json();

                const validModels = (data.models || []).filter(model => isValidModelName(model && model.id));
                if (validModels.length > 0) {
                    // `modelSelect` is now the `svc-llm-model` text input (Block 3), not a
                    // <select>, so prefill its value instead of building <option>s.
                    let currentModel = (modelSelect.value || '').trim();
                    if (!currentModel) {
                        const current = validModels.find(m => m.current);
                        const autoSelectedModel = current ? current.id : validModels[0].id;
                        modelSelect.value = autoSelectedModel;
                        currentModel = autoSelectedModel;

                        // Auto-apply the new model
                        console.log(`Auto-selected model: ${autoSelectedModel}`);
                        applyApiSettings({ showFeedback: false });
                    }

                    // Update model name in VLM output header
                    if (currentModel) {
                        const nameEl = document.getElementById('modelName');
                        if (nameEl) nameEl.textContent = currentModel;
                    }
                } else {
                    console.warn('fetchModels: no models available from', currentApiBase);
                }
            } catch (error) {
                console.error('Error fetching models:', error);
            }
        }

        // Handle model change
        modelSelect.addEventListener('change', (e) => {
            const newModel = e.target.value;
            if (!newModel) return;

            // Update model name in VLM output header (该元素已于 2026-09-18 UI 精简中移除，守卫保留以兼容)
            const modelNameEl = document.getElementById('modelName');
            if (modelNameEl) modelNameEl.textContent = newModel;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                // Send model, API base, and API key together
                websocket.send(JSON.stringify({
                    type: 'update_model',
                    model: newModel,
                    api_base: apiBaseUrl.value.trim(),
                    api_key: apiKey.value.trim()
                }));
                updateStatus(window.JoyI18n.localizeUiString('Model configured'), 'connected');
            }

            // Trigger flash animation
            modelSelect.classList.add('applied');
            setTimeout(() => {
                modelSelect.classList.remove('applied');
            }, 600);
        });

        // Also trigger flash on blur (when dropdown closes)
        modelSelect.addEventListener('blur', () => {
            const currentModel = modelSelect.value;
            if (currentModel) {
                // Trigger flash animation
                modelSelect.classList.add('applied');
                setTimeout(() => {
                    modelSelect.classList.remove('applied');
                }, 600);
            }
        });

        // Helper function to apply prompt settings
        function applyPromptSettings() {
            if (!isAnalysisRunning) {
                updateStatus('请先开始视频', 'disconnected');
                updatePromptAvailability();
                return false;
            }

            const newPrompt = cleanBackgroundQuestionText(promptText.value.trim());

            window.JoyState.currentPromptText = newPrompt;
            if (newPrompt) {
                appendPromptHistoryEntry(newPrompt);
            } else {
                pendingPromptEntry = null;
            }

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'update_prompt',
                    prompt: newPrompt
                }));
            }

            // Clear input after applying (one-shot mode)
            promptText.value = '';
            resetAsrTranscriptState('');
            resetActiveAsrSegment();
            resizePromptInput();
            return true;
        }

        function resizePromptInput() {
            promptText.style.height = 'auto';
            promptText.style.height = Math.min(promptText.scrollHeight, 120) + 'px';
        }

        function closePromptPopovers(except = null) {
            if (except !== 'presets') {
                promptPresetMenu.classList.add('hidden');
                promptPresetBtn.classList.remove('active');
            }
        }

        function flashPromptControl(element) {
            element.classList.add('applied');
            setTimeout(() => {
                element.classList.remove('applied');
            }, 600);
        }
        // P0-1 sibling (audit 2026-08-13): updatePromptAvailability() calls
        // syncSpeechButtons(), which lives in the post-main speech_input.js script
        // (loaded after this inline script). Deferring the initial paint to
        // DOMContentLoaded keeps the same load behavior while guaranteeing the
        // helper exists — an immediate call would throw ReferenceError before
        // window.JoyWs.register() runs (WS bootstrap lost).
        if (typeof syncSpeechButtons === 'function') {
            updatePromptAvailability();
        } else {
            window.addEventListener('DOMContentLoaded', () => {
                updatePromptAvailability();
            });
        }
        resizePromptInput();

        // refreshModelsBtn 绑定已删除（2026-09-19 死代码清理）：#refreshModelsBtn 已由
        // Block 3 services-panel 重构移除（"Probe" 动作覆盖重新探测），此处 `if (el)` 守卫
        // 只会恒假 → 属于「守卫掩盖的死引用」。fetchModels 仍在 load 与 API-base 变化时
        // 通过 applyApiSettings({ refreshModels: true }) 触发，行为不变。

        // Block 4: applyApiSettings + cleanupServerSession extracted to joy_ws.js (window.JoyWs)
        // Block 5: connectWebSocket extracted to joy_ws.js; the onmessage protocol router stays
        // inline as dispatchServerMessage (declared below) and is handed to JoyWs via register so
        // connectWebSocket can wire ws.onmessage. Reassigned closure vars are bridged with live
        // accessors (getWebSocket/setWebSocket/getSessionId).
        const { applyApiSettings, cleanupServerSession } = window.JoyWs;
        window.JoyWs.register({
            apiBaseUrl,
            apiKey,
            modelSelect,
            isValidModelName,
            updateStatus,
            fetchModels,
            getWebSocket: () => websocket,
            // P1-1 (audit 2026-08-13): screen_capture.js / live_ui.js / capture
            // helpers resolve the socket via window.websocket, but the lexical
            // `let websocket` is not visible on window — without this mirror the
            // screen-capture and live-camera frame pipelines short-circuit on
            // `!window.websocket`. Also clears window.websocket on close because
            // joy_ws.js calls setWebSocket(null) in onclose/reset paths.
            setWebSocket: (ws) => {
                websocket = ws;
                window.websocket = ws;
            },
            getSessionId: () => window.sessionId,
            installLlmReplyHandler,
            dispatchServerMessage
        });

        // Auto-apply API settings when API Base URL changes
        apiBaseUrl.addEventListener('blur', (e) => {
            applyApiSettings({ refreshModels: true, showFeedback: true });

            // Trigger flash animation
            apiBaseUrl.classList.add('applied');
            setTimeout(() => {
                apiBaseUrl.classList.remove('applied');
            }, 600);
        });

        apiBaseUrl.addEventListener('change', (e) => {
            applyApiSettings({ refreshModels: true, showFeedback: true });

            // Trigger flash animation
            apiBaseUrl.classList.add('applied');
            setTimeout(() => {
                apiBaseUrl.classList.remove('applied');
            }, 600);
        });

        // Auto-apply API settings when API Key changes (with debounce)
        let apiKeyDebounceTimer;
        apiKey.addEventListener('input', () => {
            clearTimeout(apiKeyDebounceTimer);
            apiKeyDebounceTimer = setTimeout(() => {
                applyApiSettings({ showFeedback: false });
            }, 1000); // Wait 1 second after user stops typing
        });

        // Flash animation when API Key loses focus
        apiKey.addEventListener('blur', () => {
            clearTimeout(apiKeyDebounceTimer);
            applyApiSettings({ showFeedback: false });

            // Trigger flash animation
            apiKey.classList.add('applied');
            setTimeout(() => {
                apiKey.classList.remove('applied');
            }, 600);
        });

        // 旧「API Presets Menu」整块已删除（2026-09-19 死代码清理）：#apiPresetsBtn /
        // #apiPresetsMenu / #apiBaseHint 在 DOM 中都不存在（2026-09-18「API Status 面板
        // 删除」+「input 面板删除」带走了整个 URL 预设快切 UI），外层 `if (a && b)` 恒假
        // → 整块不可达；块内 `.api-preset-item` 同样在 HTML 中出现 0 次。
        // 现等价物：services 面板的 Provider 预设（config_services.js 的 named presets），
        // 故按判据删除而非补回 DOM。

        // Mirror video toggle —— 已随 #mirrorBtn 一并删除（2026-09-18，用户要求）

        // Auto-apply processing interval on change
        if (processEvery) processEvery.addEventListener('change', () => {
            const interval = parseFloat(processEvery.value) || 1.0;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                // P1-3 (audit 2026-08-13): backend ws_handler.py only handles
                // `update_process_interval`; the old `update_processing` name was
                // silently dropped so the RTSP Processing Interval never applied.
                websocket.send(JSON.stringify({
                    type: 'update_process_interval',
                    process_interval: interval
                }));
            }

            // Trigger flash animation
            processEvery.classList.add('applied');
            setTimeout(() => {
                processEvery.classList.remove('applied');
            }, 600);
        });

        // Also trigger flash on blur
        if (processEvery) processEvery.addEventListener('blur', () => {
            const interval = parseFloat(processEvery.value) || 1.0;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'update_process_interval',
                    process_interval: interval
                }));
            }

            // Trigger flash animation
            processEvery.classList.add('applied');
            setTimeout(() => {
                processEvery.classList.remove('applied');
            }, 600);
        });

        // Auto-apply frames per batch on change
        if (framesPerBatch) framesPerBatch.addEventListener('change', () => {
            const value = parseInt(framesPerBatch.value) || 1;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'update_frames_per_batch',
                    frames_per_batch: value
                }));
            }

            framesPerBatch.classList.add('applied');
            setTimeout(() => {
                framesPerBatch.classList.remove('applied');
            }, 600);
        });

        if (framesPerBatch) framesPerBatch.addEventListener('blur', () => {
            const value = parseInt(framesPerBatch.value) || 1;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'update_frames_per_batch',
                    frames_per_batch: value
                }));
            }

            framesPerBatch.classList.add('applied');
            setTimeout(() => {
                framesPerBatch.classList.remove('applied');
            }, 600);
        });

        // Auto-apply max latency on change
        maxLatency.addEventListener('change', () => {
            const latency = parseFloat(maxLatency.value) || 1.0;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'update_max_latency',
                    max_latency: latency
                }));
            }
        });

        // Debug payload toggles (Settings modal): send set_debug so server includes request/response payload in vlm_response
        function sendDebugFlags() {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
            const req = document.getElementById('debugShowRequestPayload');
            const res = document.getElementById('debugShowResponsePayload');
            const mem = document.getElementById('debugShowMemoryState');
            websocket.send(JSON.stringify({
                type: 'set_debug',
                show_request_payload: !!req?.checked,
                show_response_payload: !!res?.checked,
                show_memory_state: !!mem?.checked
            }));
        }
        const debugShowRequestPayload = document.getElementById('debugShowRequestPayload');
        const debugShowResponsePayload = document.getElementById('debugShowResponsePayload');
        const debugShowMemoryState = document.getElementById('debugShowMemoryState');
        const requestPayloadDebugEl = document.getElementById('requestPayloadDebug');
        const responsePayloadDebugEl = document.getElementById('responsePayloadDebug');
        const memoryStateDebugEl = document.getElementById('memoryStateDebug');
        if (debugShowRequestPayload) {
            debugShowRequestPayload.addEventListener('change', () => {
                sendDebugFlags();
                if (requestPayloadDebugEl) requestPayloadDebugEl.style.display = debugShowRequestPayload.checked ? 'block' : 'none';
            });
        }
        if (debugShowResponsePayload) {
            debugShowResponsePayload.addEventListener('change', () => {
                sendDebugFlags();
                if (responsePayloadDebugEl) responsePayloadDebugEl.style.display = debugShowResponsePayload.checked ? 'block' : 'none';
            });
        }
        if (debugShowMemoryState) {
            debugShowMemoryState.addEventListener('change', () => {
                sendDebugFlags();
                if (memoryStateDebugEl) memoryStateDebugEl.style.display = debugShowMemoryState.checked ? 'block' : 'none';
            });
        }

        [backgroundEnabledToggle, backgroundFrameMultiplier, backgroundMaxFrames].forEach(control => {
            if (!control) return;
            control.addEventListener('change', () => {
                sendBackgroundConfig();
                control.classList?.add('applied');
                setTimeout(() => control.classList?.remove('applied'), 600);
            });
        });

        // ASR promotion toggle (joyai-asr-promotion-ui): notify the server; it echoes
        // back an `asr_promotion_updated` message which re-syncs the UI.
        if (asrPromotionToggle) {
            asrPromotionToggle.addEventListener('change', (e) => {
                settings.asrPromotionEnabled = e.target.checked;
                if (websocket && websocket.readyState === WebSocket.OPEN) {
                    websocket.send(JSON.stringify({ type: 'update_asr_promotion', enabled: e.target.checked }));
                }
                e.target.classList?.add('applied');
                setTimeout(() => e.target.classList?.remove('applied'), 600);
            });
        }

        // Memory state change detection for console logging
        let _lastMemoryStateHash = '';

        // 旧 Start / Stop 派发块与「Sidebar start/stop buttons」注释已删除
        // （2026-09-19 死代码清理）：那些注释引用的是已不存在的 startBtn / stopBtn 常量，
        // 属于同一批死引用的残留注释。真实实现是 v3.38 的每源独立按钮
        // （webcamStartBtn / rtspStartBtn / screenStartBtn，见上方 IIFE）。

        // Load on page load
        window.addEventListener('load', async () => {
            connectWebSocket();

            // Fallback for older servers that do not send server_config.
            setTimeout(() => {
                if (!serverConfigApplied) {
                    fetchModels();
                }
            }, 1000);

            // Initialize prompt display
            const initialPrompt = promptText.value.trim();
            if (initialPrompt) {
                window.JoyState.currentPromptText = initialPrompt;
            }

            // Initialize Lucide icons
            lucide.createIcons();
        });
        (function () {
            const OrigWS = window.WebSocket;
            function WrappedWS(url, protocols) {
                const ws = protocols !== undefined
                    ? new OrigWS(url, protocols)
                    : new OrigWS(url);
                try { installLlmReplyHandler(ws); } catch (e) { /* ignore */ }
                return ws;
            }
            WrappedWS.prototype = OrigWS.prototype;
            WrappedWS.CONNECTING = OrigWS.CONNECTING;
            WrappedWS.OPEN = OrigWS.OPEN;
            WrappedWS.CLOSING = OrigWS.CLOSING;
            WrappedWS.CLOSED = OrigWS.CLOSED;
            window.WebSocket = WrappedWS;

            // Also wrap any ws already in flight (e.g. created before
            // this script ran).
            try { if (window.websocket) installLlmReplyHandler(window.websocket); } catch (e) { /* ignore */ }
        })();
    