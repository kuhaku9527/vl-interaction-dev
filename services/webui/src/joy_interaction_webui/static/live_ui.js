// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        function setBtMicLevel(levelText, deviceText) {
            if (btMicLevelValue) btMicLevelValue.textContent = levelText || '--';
            if (deviceText !== undefined && btMicDeviceValue) btMicDeviceValue.textContent = window.JoyI18n.localizeDeviceLabel(deviceText) || '--';
        }

        function stopBtMicLevelMonitor() {
            if (btMicLevelFrame) {
                cancelAnimationFrame(btMicLevelFrame);
                btMicLevelFrame = null;
            }
            if (btMicLevelSource) {
                try { btMicLevelSource.disconnect(); } catch (_) {}
                btMicLevelSource = null;
            }
            if (btMicLevelAudioContext) {
                btMicLevelAudioContext.close().catch(() => {});
                btMicLevelAudioContext = null;
            }
            btMicLevelAnalyser = null;
            btMicLevelBuffer = null;
            setBtMicLevel('--', '--');
        }

        function startBtMicLevelMonitor(stream) {
            stopBtMicLevelMonitor();
            const track = stream?.getAudioTracks?.()[0];
            const label = track?.label || 'mic';
            const shortLabel = label.replace(/^默认 - /, '').replace(/^通讯 - /, '');
            setBtMicLevel('0%', shortLabel);
            try {
                const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
                if (!AudioContextCtor || !stream) return;
                btMicLevelAudioContext = new AudioContextCtor();
                btMicLevelSource = btMicLevelAudioContext.createMediaStreamSource(stream);
                btMicLevelAnalyser = btMicLevelAudioContext.createAnalyser();
                btMicLevelAnalyser.fftSize = 512;
                btMicLevelBuffer = new Uint8Array(btMicLevelAnalyser.fftSize);
                btMicLevelSource.connect(btMicLevelAnalyser);
                const tick = () => {
                    if (!btMicLevelAnalyser || !btMicLevelBuffer) return;
                    btMicLevelAnalyser.getByteTimeDomainData(btMicLevelBuffer);
                    let sum = 0;
                    for (let i = 0; i < btMicLevelBuffer.length; i += 1) {
                        const v = (btMicLevelBuffer[i] - 128) / 128;
                        sum += v * v;
                    }
                    const rms = Math.sqrt(sum / Math.max(1, btMicLevelBuffer.length));
                    const percent = Math.min(100, Math.round(rms * 220));
                    setBtMicLevel(percent <= 1 ? 'silent' : `${percent}%`);
                    btMicLevelFrame = requestAnimationFrame(tick);
                };
                tick();
            } catch (error) {
                console.warn('BT mic level monitor unavailable:', error);
                setBtMicLevel('n/a', shortLabel);
            }
        }
        // -------------------------------------------------------------------
        // C.B live visual + proactive speak (spec draft-live-visual-cb.md §3
        // 层 3). The live panel can open a 1fps visual feed (screen capture
        // via screen_capture.js OR camera via an inline getUserMedia sender —
        // single select, never both) that ships WS `frame` messages over the
        // SAME main websocket; server.py forwards them to the live session
        // ring buffer when a live session is active. The proactive switch
        // calls POST /api/live/proactive at runtime (env gate is the final
        // fallback).
        // -------------------------------------------------------------------
        function setLiveVideoHint(text) {
            if (liveVideoHint) liveVideoHint.textContent = text || '';
        }

        function setLiveProactiveHint(text) {
            if (liveProactiveHint) liveProactiveHint.textContent = text || '';
        }

        function setLiveVideoActive(active) {
            liveVideoActive = Boolean(active);
            if (!liveVideoBtn) return;
            liveVideoBtn.classList.toggle('listening', liveVideoActive);
            const label = liveVideoBtn.querySelector('.live-video-label');
            if (label) label.textContent = liveVideoActive ? '关画面' : '开画面';
            liveVideoBtn.title = liveVideoActive
                ? '关闭画面（停止帧推送）'
                : '打开画面（屏幕/摄像头，1fps 帧推送）';
            if (liveVideoSourceEl) liveVideoSourceEl.disabled = !liveModeActive || liveVideoActive;
            lucide.createIcons();
        }

        function setLiveProactiveUiState() {
            // The switch is usable only while live is active AND the env gate
            // (LIVE_PROACTIVE_ENABLED) is on; otherwise it stays disabled with
            // an env hint so the operator knows how to turn the feature on.
            if (!liveProactiveToggle) return;
            const usable = liveModeActive && liveProactiveSupported;
            liveProactiveToggle.disabled = !usable;
            if (!usable) {
                liveProactiveToggle.checked = false;
                if (liveModeActive) {
                    setLiveProactiveHint('需在 run-windows.env 开启 LIVE_PROACTIVE_ENABLED=true');
                }
            }
        }

        function stopLiveCameraCapture() {
            const streamRef = liveCameraStream;
            if (liveCameraInterval) {
                clearInterval(liveCameraInterval);
                liveCameraInterval = null;
            }
            if (streamRef) {
                streamRef.getTracks().forEach(track => track.stop());
            }
            liveCameraStream = null;
            liveCameraFrameSeq = 0;
            if (typeof videoElement !== 'undefined' && videoElement && videoElement.srcObject === streamRef) {
                videoElement.srcObject = null;
            }
        }

        async function startLiveCameraCapture() {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error('当前浏览器不支持摄像头采集');
            }
            const constraints = { width: { ideal: 960 }, height: { ideal: 540 } };
            const sel = document.getElementById('cameraSelect');
            if (sel && sel.value) constraints.deviceId = { exact: sel.value };
            liveCameraStream = await navigator.mediaDevices.getUserMedia({
                video: constraints,
                audio: false
            });
            const track = liveCameraStream.getVideoTracks()[0];
            if (track) {
                track.addEventListener('ended', () => stopLiveVideoCapture());
            }
            if (typeof videoElement !== 'undefined' && videoElement) {
                videoElement.classList.remove('mirrored');
                videoElement.srcObject = liveCameraStream;
            }
            liveCameraFrameSeq = 0;
            liveCameraInterval = setInterval(() => {
                if (!window.websocket || window.websocket.readyState !== WebSocket.OPEN) return;
                if (typeof videoElement === 'undefined' || !videoElement
                    || videoElement.readyState < 2 || videoElement.videoWidth <= 0) {
                    return;
                }
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = videoElement.videoWidth;
                    canvas.height = videoElement.videoHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
                    const jpegDataUrl = canvas.toDataURL('image/jpeg', 0.92);
                    const base64 = jpegDataUrl.split(',')[1];
                    liveCameraFrameSeq += 1;
                    window.websocket.send(JSON.stringify({
                        type: 'frame',
                        format: 'jpeg',
                        width: canvas.width,
                        height: canvas.height,
                        data: base64,
                        timestamp: Date.now(),
                        source: 'camera',
                        frame_seq: liveCameraFrameSeq
                    }));
                } catch (err) {
                    console.error('Live camera frame error:', err);
                }
            }, 1000);
        }

        async function startLiveVideo() {
            if (!liveModeActive || liveVideoActive) return;
            setLiveVideoHint('');
            try {
                if (!window.websocket || window.websocket.readyState !== WebSocket.OPEN) {
                    if (typeof connectWebSocket === 'function') connectWebSocket();
                }
                const source = liveVideoSourceEl ? liveVideoSourceEl.value : 'screen';
                if (source === 'camera') {
                    await startLiveCameraCapture();
                    liveVideoOwned = true;
                    setLiveVideoHint('摄像头画面已开启（1fps 帧推送）');
                } else {
                    // Reuse the existing screen_capture.js pipeline (1fps JPEG
                    // -> WS `frame` over the live session's WS). If the main
                    // video panel already runs a capture we adopt it without
                    // owning it, so leaving live mode never kills a capture the
                    // operator started elsewhere.
                    if (window.isScreenCapturing && window.isScreenCapturing()) {
                        liveVideoOwned = false;
                    } else {
                        await window.startScreenCapture(window.websocket, { fps: 1 });
                        liveVideoOwned = true;
                    }
                    const previewStream = window.getScreenCaptureStream && window.getScreenCaptureStream();
                    if (previewStream && typeof videoElement !== 'undefined' && videoElement) {
                        videoElement.classList.remove('mirrored');
                        videoElement.srcObject = previewStream;
                    }
                    setLiveVideoHint('屏幕画面已开启（1fps 帧推送）');
                }
                setLiveVideoActive(true);
            } catch (err) {
                console.error('startLiveVideo:', err);
                stopLiveCameraCapture();
                liveVideoOwned = false;
                setLiveVideoActive(false);
                setLiveVideoHint('画面开启失败：' + (err && err.message ? err.message : err));
            }
        }

        function stopLiveVideoCapture() {
            if (liveVideoOwned) {
                if (window.isScreenCapturing && window.isScreenCapturing()) {
                    const screenStream = window.getScreenCaptureStream && window.getScreenCaptureStream();
                    if (typeof window.stopScreenCapture === 'function') window.stopScreenCapture();
                    if (screenStream && typeof videoElement !== 'undefined' && videoElement
                        && videoElement.srcObject === screenStream) {
                        videoElement.srcObject = null;
                    }
                }
            }
            stopLiveCameraCapture();
            liveVideoOwned = false;
            setLiveVideoActive(false);
            setLiveVideoHint('');
        }

        async function toggleLiveProactive() {
            if (!liveModeActive || !liveProactiveToggle) return;
            const enabled = liveProactiveToggle.checked;
            try {
                const resp = await fetch('/api/live/proactive', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, enabled })
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.applied === false) {
                    liveProactiveToggle.checked = false;
                    setLiveProactiveHint((data.error || '主动搭话切换失败（需 LIVE_PROACTIVE_ENABLED=true）'));
                    return;
                }
                if (data.supported === false) {
                    liveProactiveSupported = false;
                    setLiveProactiveUiState();
                    setLiveProactiveHint('需在 run-windows.env 开启 LIVE_PROACTIVE_ENABLED=true');
                    return;
                }
                liveProactiveSupported = true;
                setLiveProactiveHint(enabled
                    ? '主动搭话已开启（画面变化时 AI 可能主动开口）'
                    : '主动搭话已关闭');
            } catch (err) {
                console.error('toggleLiveProactive failed:', err);
                liveProactiveToggle.checked = !enabled;
                setLiveProactiveHint('主动搭话切换失败');
            }
        }

        function setLiveModeActive(active) {
            liveModeActive = Boolean(active);
            if (!liveModeBtn) return;
            liveModeBtn.classList.toggle('listening', liveModeActive);
            liveModeBtn.setAttribute('aria-checked', liveModeActive ? 'true' : 'false');
            liveModeBtn.title = liveModeActive ? '退出 Live 常驻模式（点击可切换回 Jarvis 唤醒）' : '进入 Live 常驻模式';
            liveModeBtn.disabled = Boolean(liveModeStarting && !liveModeActive);
            if (liveEnrollBtn) {
                // 注册声音 only makes sense while the live session is active.
                liveEnrollBtn.disabled = !liveModeActive || liveEnrollActive;
            }
            if (liveVideoBtn) {
                liveVideoBtn.disabled = !liveModeActive;
            }
            if (liveVideoSourceEl) {
                liveVideoSourceEl.disabled = !liveModeActive;
            }
            if (!liveModeActive) {
                // Leaving live mode: stop any live visual capture (screen or
                // camera) and reset the proactive switch. Idempotent — also
                // reached from the startLiveMode failure path.
                stopLiveVideoCapture();
                if (liveProactiveToggle) liveProactiveToggle.checked = false;
                setLiveProactiveHint('');
            }
            setLiveProactiveUiState();
            lucide.createIcons();
        }

        function setLiveEnrollActive(active) {
            liveEnrollActive = Boolean(active);
            if (!liveEnrollBtn) return;
            liveEnrollBtn.classList.toggle('listening', liveEnrollActive);
            liveEnrollBtn.disabled = !liveModeActive || liveEnrollActive;
            liveEnrollBtn.title = liveEnrollActive
                ? '正在注册声音…（说 3 遍：你好我是用户）'
                : '注册你的声音（Addressee 过滤：只响应你）';
            const label = liveEnrollBtn.querySelector('.live-enroll-label');
            if (label) {
                label.textContent = liveEnrollActive ? '注册中…' : (liveEnrollCompleted ? '已注册 ✓' : '注册声音');
            }
            lucide.createIcons();
        }

        function setLiveEnrollHint(text) {
            if (liveEnrollHint) liveEnrollHint.textContent = text || '';
        }

        async function startLiveEnroll() {
            if (!liveModeActive || liveEnrollActive) return;
            setLiveEnrollHint('');
            try {
                const resp = await fetch('/api/live/enroll', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, action: 'start' })
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.started) {
                    setLiveEnrollHint((data.error || '注册不可用（Addressee 未启用）'));
                    return;
                }
                liveEnrollCompleted = false;
                setLiveEnrollActive(true);
                setLiveEnrollHint('说 3 遍：你好，我是用户（每遍间隔停顿）…');
                let tries = 0;
                liveEnrollTimer = setInterval(async () => {
                    tries += 1;
                    try {
                        const stResp = await fetch('/api/live/status?session_id=' + encodeURIComponent(sessionId));
                        const st = await stResp.json().catch(() => ({}));
                        if (st && st.enroll_segment_count >= 3) {
                            await finishLiveEnroll();
                            return;
                        }
                        if (tries >= 25) {  // ~25s safety timeout
                            await finishLiveEnroll();
                        }
                    } catch (error) {
                        console.warn('live enroll poll failed:', error);
                    }
                }, 1000);
            } catch (error) {
                console.error('Error starting live enroll:', error);
                setLiveEnrollHint('注册启动失败');
            }
        }

        async function finishLiveEnroll() {
            if (liveEnrollTimer) {
                clearInterval(liveEnrollTimer);
                liveEnrollTimer = null;
            }
            if (!liveEnrollActive) return;
            setLiveEnrollActive(false);
            try {
                const resp = await fetch('/api/live/enroll', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, action: 'finish' })
                });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.enrolled) {
                    liveEnrollCompleted = true;
                    setLiveEnrollHint('声音注册成功，现在只响应你');
                } else {
                    setLiveEnrollHint('注册失败：' + (data.error || '需要至少 2 段有效语音'));
                }
            } catch (error) {
                console.error('Error finishing live enroll:', error);
                setLiveEnrollHint('注册失败');
            }
        }

        async function cancelLiveEnroll() {
            if (liveEnrollTimer) {
                clearInterval(liveEnrollTimer);
                liveEnrollTimer = null;
            }
            if (!liveEnrollActive) return;
            liveEnrollActive = false;
            try {
                await fetch('/api/live/enroll', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, action: 'cancel' })
                });
            } catch (error) {
                console.warn('live enroll cancel failed:', error);
            }
            setLiveEnrollActive(false);
            setLiveEnrollHint('');
        }

        async function startLiveMode() {
            if (liveModeActive || liveModeStarting) return;
            liveModeStarting = true;
            try {
                const startResp = await fetch('/api/live/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId })
                });
                if (!startResp.ok) {
                    const err = await startResp.json().catch(() => ({}));
                    throw new Error(err.error || ('Live 启动失败：HTTP ' + startResp.status));
                }
                const startData = await startResp.json().catch(() => ({}));
                if (typeof startData.proactive_supported === 'boolean') {
                    liveProactiveSupported = startData.proactive_supported;
                }
                if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                    connectWebSocket();
                }
                liveModeStream = await navigator.mediaDevices.getUserMedia({ audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: false,
                    autoGainControl: false
                }, video: false });
                liveModePeerConnection = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                liveModePeerConnection.oniceconnectionstatechange = () => {
                    const state = liveModePeerConnection?.iceConnectionState;
                    console.log('Live ICE state:', state);
                    if (['disconnected', 'failed', 'closed'].includes(state)) {
                        stopLiveMode({ notifyServer: state !== 'closed' });
                    }
                };
                const audioTransceiver = liveModePeerConnection.addTransceiver('audio', { direction: 'sendrecv' });
                const liveAudioTrack = liveModeStream.getAudioTracks()[0];
                if (liveAudioTrack) {
                    await audioTransceiver.sender.replaceTrack(liveAudioTrack);
                }
                const offer = await liveModePeerConnection.createOffer();
                await liveModePeerConnection.setLocalDescription(offer);
                const response = await fetch('/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        sdp: liveModePeerConnection.localDescription.sdp,
                        type: liveModePeerConnection.localDescription.type,
                        session_id: sessionId,
                        live_audio: true
                    })
                });
                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.error || ('Live 启动失败：HTTP ' + response.status));
                }
                const answer = await response.json();
                await liveModePeerConnection.setRemoteDescription(new RTCSessionDescription(answer));
                setLiveModeActive(true);
                updateStatus('Live 常驻监听中', 'connected');
            } catch (error) {
                console.error('Error starting Live mode:', error);
                updateStatus(getMicErrorMessage(error), 'disconnected');
                await stopLiveMode({ notifyServer: false });
            } finally {
                liveModeStarting = false;
                setLiveModeActive(Boolean(liveModePeerConnection && liveModeStream));
            }
        }

        async function stopLiveMode({ notifyServer = true } = {}) {
            liveModeStarting = false;
            stopLlmReplyAudio();
            if (liveEnrollActive) {
                await cancelLiveEnroll();
            }
            if (liveModePeerConnection) {
                liveModePeerConnection.oniceconnectionstatechange = null;
                liveModePeerConnection.close();
                liveModePeerConnection = null;
            }
            if (liveModeStream) {
                liveModeStream.getTracks().forEach(track => track.stop());
                liveModeStream = null;
            }
            setLiveModeActive(false);
            if (notifyServer) {
                try {
                    await fetch('/api/live/stop', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ session_id: sessionId })
                    });
                } catch (error) {
                    console.warn('Live stop failed:', error);
                }
            }
            updateStatus(window.JoyI18n.localizeUiString('Connected'), 'connected');
        }

        function setBtListeningActive(active) {
            btListening = Boolean(active);
            if (!btListenBtn) return;
            btListenBtn.classList.toggle('listening', btListening);
            btListenBtn.setAttribute('aria-checked', btListening ? 'true' : 'false');
            btListenBtn.title = btListening ? '停止监听 BT 唤醒词（点击可切换回 Live 常驻）' : '监听 BT 唤醒词';
            btListenBtn.disabled = Boolean(btListeningStarting && !btListening);
            lucide.createIcons();
        }

        function attachBtListenRemoteAudio(event) {
            if (!btListenPlayer || event.track.kind !== 'audio') return;
            const remoteStream = event.streams && event.streams[0]
                ? event.streams[0]
                : new MediaStream([event.track]);
            btListenPlayer.srcObject = remoteStream;
            btListenPlayer.muted = false;
            btListenPlayer.play().catch(error => {
                console.warn('BT listening audio play blocked:', error);
            });
        }

        async function startBtListening() {
            if (btListening || btListeningStarting) return;
            btListeningStarting = true;
            try { await fetch('/api/jarvis/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId }) }); } catch (e) { console.warn('reset jarvis session failed', e); }
            setBtListeningActive(true);
            try {
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                    throw new Error('当前浏览器不支持麦克风采集，或页面没有麦克风权限。');
                }
                if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                    connectWebSocket();
                }
                btListenStream = await navigator.mediaDevices.getUserMedia({ audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: false,
                    autoGainControl: false
                }, video: false });
                const audioTrack = btListenStream.getAudioTracks()[0];
                startBtMicLevelMonitor(btListenStream);
                // Gain boost must run when a REAL mic track exists, otherwise the
                // user-selected multiplier (default 1.5x from the GAIN slider) never
                // reaches KWS. The previous condition was inverted (`if (!audioTrack)`),
                // so it only applied when there was NO track.
                if (audioTrack) {
                // Apply user-selected mic gain via Web Audio API so KWS sees a stronger signal.
                // Uses a separate AudioContext so the AnalyserNode tap in startBtMicLevelMonitor still works.
                btListenGainNode = null;
                try {
                    const gainValue = parseFloat(document.getElementById('btMicGainSelect').value || '1.0');
                    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
                    btMicGainAudioContext = new AudioContextCtor();
                    const source = btMicGainAudioContext.createMediaStreamSource(btListenStream);
                    const gain = btMicGainAudioContext.createGain();
                    gain.gain.value = gainValue;
                    const destination = btMicGainAudioContext.createMediaStreamDestination();
                    btMicGainAudioContext._btGainRef = gain;
                    source.connect(gain).connect(destination);
                    // Store for live gain change (5042); must be set inside this try
                    // where `gain` is in scope to avoid a ReferenceError.
                    btListenGainNode = gain;
                    const boostedTrack = destination.stream.getAudioTracks()[0];
                    if (boostedTrack) {
                        // KWS sees the boosted signal; mic level monitor keeps watching the raw stream so the display matches what user hears.
                        boostedTrack._btOriginalAudioTrack = audioTrack;
                        audioTrack = boostedTrack;
                    }
                } catch (gainErr) {
                    console.warn('Mic gain init failed, using raw track:', gainErr);
                }
                }
                btListenPeerConnection = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                btListenPeerConnection.ontrack = attachBtListenRemoteAudio;
                btListenPeerConnection.oniceconnectionstatechange = () => {
                    const state = btListenPeerConnection?.iceConnectionState;
                    console.log('BT listening ICE state:', state);
                    if (['disconnected', 'failed', 'closed'].includes(state)) {
                        stopBtListening({ notifyServer: state !== 'closed' });
                    }
                };
                const audioTransceiver = btListenPeerConnection.addTransceiver('audio', { direction: 'sendrecv' });
                await audioTransceiver.sender.replaceTrack(audioTrack);
                const offer = await btListenPeerConnection.createOffer();
                await btListenPeerConnection.setLocalDescription(offer);
                const response = await fetch('/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        sdp: btListenPeerConnection.localDescription.sdp,
                        type: btListenPeerConnection.localDescription.type,
                        session_id: sessionId,
                        jarvis_audio: true
                    })
                });
                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.error || `监听启动失败：HTTP ${response.status}`);
                }
                const answer = await response.json();
                await btListenPeerConnection.setRemoteDescription(new RTCSessionDescription(answer));
                updateStatus(window.JoyI18n.localizeUiString('BT listening'), 'connected');
                setBtListeningActive(true);
            } catch (error) {
                console.error('Error starting BT listening:', error);
                updateStatus(getMicErrorMessage(error), 'disconnected');
                await stopBtListening({ notifyServer: false });
            } finally {
                btListeningStarting = false;
                setBtListeningActive(Boolean(btListenPeerConnection && btListenStream));
            }
        }

        async function stopBtListening({ notifyServer = true } = {}) {
            btListeningStarting = false;
            stopBtMicLevelMonitor();
            if (btListenPeerConnection) {
                btListenPeerConnection.oniceconnectionstatechange = null;
                btListenPeerConnection.ontrack = null;
                btListenPeerConnection.close();
                btListenPeerConnection = null;
            }
            if (btListenStream) {
                btListenStream.getTracks().forEach(track => track.stop());
                btListenStream = null;
            btListenGainNode = null;
            if (btMicGainAudioContext) {
                try { await btMicGainAudioContext.close(); } catch (_) {}
                btMicGainAudioContext = null;
            }
            }
            if (btListenPlayer) {
                btListenPlayer.pause();
                btListenPlayer.srcObject = null;
            }
            setBtListeningActive(false);
            if (notifyServer) {
                try {
                    await fetch('/api/jarvis/stop', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ session_id: sessionId })
                    });
                } catch (error) {
                    console.warn('BT listening stop failed:', error);
                }
            }
            updateStatus(window.JoyI18n.localizeUiString('Connected'), 'connected');
        }
        // -------------------------------------------------------------------
        // Mode selection is a RADIO GROUP: "Jarvis 唤醒" and "Live 常驻" are
        // mutually exclusive. The ONLY way to switch is an explicit user click
        // on the other mode button — clicking one mode stops the other first.
        // There is deliberately NO automatic switch path (no timer, state
        // watcher, or status callback ever toggles a mode on its own).
        // Each selector re-checks the other mode's active/starting flags right
        // after its awaited stop-other call: a rapid toggle can re-start the
        // other mode while the stop fetch is in flight, and the losing handler
        // must give up so both modes can never run at once (single-mode
        // invariant, PRD mutual exclusion).
        // -------------------------------------------------------------------
        async function selectBtListenMode() {
            if (btListening || btListeningStarting) {
                await stopBtListening();
                return;
            }
            if (liveModeActive || liveModeStarting) {
                await stopLiveMode();
                if (liveModeActive || liveModeStarting) {
                    return;
                }
            }
            await startBtListening();
        }

        async function selectLiveMode() {
            if (liveModeActive || liveModeStarting) {
                await stopLiveMode();
                return;
            }
            if (btListening || btListeningStarting) {
                await stopBtListening();
                if (btListening || btListeningStarting) {
                    return;
                }
            }
            await startLiveMode();
        }

        btListenBtn.addEventListener('click', selectBtListenMode);
        liveModeBtn.addEventListener('click', selectLiveMode);
        if (liveEnrollBtn) {
            liveEnrollBtn.addEventListener('click', startLiveEnroll);
        }
        if (liveVideoBtn) {
            liveVideoBtn.addEventListener('click', () => {
                if (liveVideoActive) {
                    stopLiveVideoCapture();
                } else {
                    startLiveVideo();
                }
            });
        }
        if (liveVideoSourceEl) {
            liveVideoSourceEl.addEventListener('change', () => {
                // Single-select (spec §4 边界): a source change while capturing
                // is ignored — the operator must close the current feed first.
                if (liveVideoActive) {
                    setLiveVideoHint('请先关闭当前画面再切换来源');
                } else {
                    setLiveVideoHint('');
                }
            });
        }
        if (liveProactiveToggle) {
            liveProactiveToggle.addEventListener('change', toggleLiveProactive);
        }
        promptSendBtn.addEventListener('click', () => {
            sendBtPrompt();
        });

        // BT-7274 multimodal paper-plane: text + current captured frame (if any).
        // The server replies with an llm_reply WS event; we then fetch
        // /api/tts/synthesize and play the resulting WAV via <audio>.
        // v3.35: when a screen capture (or webcam) video source is live, we
        // snapshot the latest frame and ship it as image_b64 so the LLM can
        // ground its answer in what BT-7274 is supposedly looking at. If no
        // video source is active the server falls back to text-only.
        const btTtsPlayer = document.getElementById('btTtsPlayer');
        async function captureBtFrameB64() {
            try {
                const v = (typeof window.getScreenCaptureVideo === 'function'
                            && window.getScreenCaptureVideo())
                          || (typeof videoElement !== 'undefined' ? videoElement : null);
                if (!v || !v.videoWidth || !v.videoHeight) return null;
                const sw = v.videoWidth, sh = v.videoHeight;
                const targetW = 800;
                const scale = sw > targetW ? targetW / sw : 1;
                const w = Math.max(1, Math.round(sw * scale));
                const h = Math.max(1, Math.round(sh * scale));
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(v, 0, 0, w, h);
                const dataUrl = canvas.toDataURL('image/jpeg', 0.7);
                const commaIdx = dataUrl.indexOf(',');
                return commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : null;
            } catch (err) {
                console.warn('[bt-send] captureBtFrameB64 failed:', err);
                return null;
            }
        }
        async function sendBtPrompt() {
            const text = (promptText.value || '').trim();
            if (!text) {
                promptText.focus();
                return;
            }
            resetBtTurnLatency();
            btLatency.sendStartAt = performance.now();
            if (isSpeechActive() || asrStream || asrAudioContext || asrWs) {
                if (!btLatency.asrFinalAt) btLatency.asrFinalAt = performance.now();
                await stopSpeech({ sendEnd: false, sendPrompt: false });
            }
            renderBtLatency();
            appendPilotToResult(text);
            promptText.value = '';
            resetAsrTranscriptState('');
            try {
                const image_b64 = await captureBtFrameB64();
                const payload = { text: text, session_id: sessionId };
                if (image_b64) payload.image_b64 = image_b64;
                const resp = await fetch('/api/llm/message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                btLatency.sendAckAt = performance.now();
                renderBtLatency();
                if (!resp.ok) {
                    appendJarvisToResult(`[bt-send] POST /api/llm/message failed: HTTP ${resp.status}`, 'error');
                }
            } catch (err) {
                appendJarvisToResult(`[bt-send] POST /api/llm/message error: ${err && err.message ? err.message : err}`, 'error');
            }
            promptText.value = '';
            resetAsrTranscriptState('');
            flashPromptControl(promptText);
        }
        // Allow Cmd/Ctrl+Enter to send to BT-7274.
        promptText.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                sendBtPrompt();
            }
        });

if (typeof window !== 'undefined') {
    window.JoyLiveUi = {
        startLiveMode,
        stopLiveMode,
        setLiveModeActive,
        startLiveVideo,
        stopLiveVideoCapture,
        toggleLiveProactive,
        startBtListening,
        stopBtListening,
        setBtListeningActive,
        startLiveEnroll,
        finishLiveEnroll,
        cancelLiveEnroll,
        captureBtFrameB64,
        sendBtPrompt,
        selectLiveMode,
        selectBtListenMode
    };
}
