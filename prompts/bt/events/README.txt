# Event response audio files for BT-7274 (Jarvis mode)
# ===================================================
# wake.wav    = "我在，铁驭"（2026-08-17 已用 MiniMax TTS 生成，取代旧占位）
# goodbye.wav = "任务完成，断开神经链接"
# error.wav   = "铁驭，必须先建立神经链接才能继续"（copy from ref_audio/1.BT-7274/）
#
# 措辞规范（2026-08-17 用户拍板）：
#   角色名统一为「铁驭」（泰坦陨落官方译名）。旧占位/旧脚本里的「铁御」是错的，
#   已废弃——见 generate_event_audio.py 的 EVENTS 文本与本文档。
#
# 重新生成事件音效（真实音色）：
#   1. 确保 MINIMAX_API_KEY + MINIMAX_GROUP_ID 已配置（User env）
#   2. voice_clone_api（8985）在跑且连上 MiniMax
#   3. 运行：python services/scripts/generate_event_audio.py --voice-id minimax_man33333
#      （注册参考音频为 MiniMax 音色，然后合成 wake + goodbye）
#
# 无线电静默唤醒复用点（doc/specs/draft-radio-silence.md §5，零 token）：
#   jarvis_mode._play_wake_wav() -> _play_event_wav(config.wake_wav) 播放本文件；
#   静默唤醒（wake_from_silence / silence.wake）经 WebUI -> WS silence_wake 事件
#   由浏览器播放同一文件。缺文件只记日志不炸。
