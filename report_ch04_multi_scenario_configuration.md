# Chapter 4: 多场景配置矩阵对照 — 从 2 场景到 7 场景的参数化体系

草稿包含"直播/贾维斯配置矩阵"，覆盖直播和语音助手两个场景。这一设计与业界实践相比，在场景覆盖度、参数粒度和动态切换能力三个维度上存在显著差距。基于对 OpenAI Realtime API、Google Gemini Live、LiveKit Agents、Deepgram、ElevenLabs、Vapi 和 Agora ConvoAI 七大平台的调研，业界已形成覆盖七大类场景的成熟配置体系。

## 4.1 场景覆盖度：从 2 到 7

草稿的"直播"和"贾维斯"（语音助手）两个场景是重要的起点，但远未覆盖语音 AI 的主要应用场景。基于业界实践，完整的场景分类应至少包含七类：直播（单向为主，偶尔互动）、语音助手（指令式，短交互，极低延迟）、实时对话（双向自然对话，自适应打断）、会议转录（多说话人，连续转录，说话人分离）、客服（任务导向，保守端点检测，需要 backchannel）、教育（引导式，给学生思考时间，关闭打断）和面试（严格结构化，手动 turn 控制）。

每个场景在 VAD 参数、Turn Decision 参数和 Interruption 参数三个维度上有截然不同的最优配置。例如，直播场景需要高 VAD 阈值（0.7–0.9）和关闭打断，而语音助手需要低 VAD 阈值（0.3–0.5）和激进端点检测（silence_duration_ms 200–400ms）。客服场景则需要较长的静音等待（700–1000ms）和自适应打断，以确保不打断客户的完整表达。

## 4.2 参数粒度：三维度参数族

业界实践将配置参数组织为三个维度，每个维度包含 3–5 个关键参数。VAD 参数族包括 `threshold`（语音检测灵敏度）、`silence_duration_ms`（判定语音结束的静音时长）、`prefix_padding_ms`（语音开始前保留的音频）和 `speech_duration_ms`（有效语音的最短时长）。Turn Decision 参数族包括 `eagerness`（结束 turn 的激进程度）、`min_delay`/`max_delay`（turn 判定后的等待时间范围）、`eot_threshold`（End-of-Turn 置信度阈值）和 `eager_eot_threshold`（预判 End-of-Turn 的置信度阈值）。Interruption 参数族包括 `barge_in_enabled`（是否允许打断）、`mode`（vad/adaptive）、`min_duration`（最短语音时长才算有效打断）和 `false_interruption_timeout`（误打断检测超时）。

草稿需要明确是否覆盖了这三个维度，以及每个维度的参数粒度是否足够。特别值得关注的是 Deepgram 的 Eager EOT 机制——在用户可能快说完时（中等置信度）就触发 `EagerEndOfTurn`，让 LLM 提前开始推理，如果用户继续说则触发 `TurnResumed` 取消预生成。这一机制可以节省数百毫秒延迟，是草稿配置矩阵中值得引入的高级参数。

## 4.3 预设体系设计

Vapi 提供了业界最成熟的预设体系，通过 `startSpeakingPlan` 和 `stopSpeakingPlan` 控制，包含 Aggressive、Normal 和 Conservative 三档预设。Aggressive 预设的 `waitFunction` 在 50% 置信度时仅等待约 200ms，适合客服和游戏场景；Conservative 预设则等待约 2700ms，适合医疗和正式场合。

建议草稿引入类似的预设体系，至少包含七个预设（对应七类场景），每个预设覆盖 VAD、Turn Decision 和 Interruption 三个维度的默认参数，同时允许逐参数覆盖。预设应支持版本化，便于灰度升级和 A/B 测试。

## 4.4 动态场景切换

OpenAI Realtime API 支持通过 `session.update` 在会话中动态修改 turn_detection 配置，无需重连。LiveKit Agents 也支持运行时更新 endpointing 参数。草稿应考虑支持三种动态切换模式：手动切换（用户或开发者通过 API 切换 preset）、条件切换（基于会话特征自动切换，如检测到多人→切换到会议模式）和渐进切换（在对话中根据用户行为渐变参数，如用户多次被打断→自动降低打断灵敏度）。

场景自动识别方面，虽然当前业界尚无成熟的"场景自动分类器"产品，但可以从音频通道数、用户说话占比、平均 utterance 长度、打断频率和静音段分布等信号推断场景。推荐采用"显式声明加自动推断"混合模式——优先使用开发者显式指定的场景，无显式声明时基于信号自动推断并应用对应 preset，推断结果以较低置信度应用（偏保守）。

## 4.5 对照总结

草稿的"直播/贾维斯配置矩阵"是良好的起点，但需要从两个场景扩展到七个场景，从粗粒度参数扩展到三维度参数族，并引入预设体系、动态切换和场景自动识别能力。完整的七场景三维度配置矩阵参考值已在研究附录中提供，可直接用于草稿的配置矩阵扩展。下一章将分析草稿的中断/打断机制设计。
