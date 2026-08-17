# Handoff — WebUI 优化（独立组负责）

> 日期：2026-08-16
> 职责边界：**WebUI 前端优化由独立对话/组负责**。本文件是给 UI 优化组的
> 跨对话上下文（工作区 SSOT），主对话只做后端/架构，不抢 UI 改动。
> 按项目惯例：跨对话靠落盘文件通信，不假定对方读本对话。

---

## 1. 子代理模型指定（UI 组最可能踩的坑）

**结论：WorkBuddy 不支持"按子代理指定任意模型"**（Agent 工具 model 只有
`default` / `lite` / `reasoning` 三档）。本地 `~/.workbuddy/models.json` /
`settings.json` / `app-config.json` 均无 agent 级模型映射；官方文档
`/docs/workbuddy/Agent`、`/Model` 均 404。

### 可行方案 B：会话级切模型 + 子代理继承

```
派前端子代理前 → 会话模型切到 MiniMax-M3（模型选择器）
派子代理（model 用 default，继承 M3）→ 子代理直接看图（内建视觉）
前端子代理完成 → 模型切回原样
```

- **MiniMax-M3 已注册**：`~/.workbuddy/models.json`（vendor Custom、
  `supportsImages: true`、`supportsToolCall: true`、`supportsReasoning: true`，
  endpoint `https://api.minimaxi.com/v1`）——子代理用 M3 = 原生多模态，
  **不需要图片识别 skill**（读截图/设计稿直接看）。
- 其他子代理照旧：不切模型时派（default 继承当前默认模型）。

## 2. 已配置好的相关资产（UI 组可直接用）

| 资产 | 位置 | 说明 |
|---|---|---|
| 图片理解 skill | `~/.workbuddy/skills/minimax-understand-image__skillhub/` | `scripts/understand_image.py` **默认 OpenRouter 免费后端**（gemma-4-26b:free，cost=0）；`--backend minimax` 切 MiniMax MCP。key：OPENROUTER_API_KEY / MINIMAX_API_KEY（均在 User env） |
| summary 槽位 provider | `services/webui/.../services_config.py` | summary 默认 `openrouter`（gemma-4-26b:free）；`_PROVIDER_CHOICES` summary=(minimax, openrouter)；前端 summary 有 provider 下拉联动（SUMMARY_PRESETS） |
| OpenRouter key | User env `OPENROUTER_API_KEY` | 免费层 50 req/day + 20/min（429 计入配额，见 spec 留痕） |

## 3. ⚠️ 前端热切能力现状（UI 组必读：已实现 vs 未暴露）

**后端热切能力已全部实现**（2026-08-15/16 主对话完成），前端 API 全部就绪：

| 能力 | 前端可消费的 API | 状态 |
|---|---|---|
| 6 槽位配置（llm/summary/tts/asr/agent/embedding） | GET/PUT `/api/services/config` | ✅ 后端 |
| 服务探活（含 agent/embedding） | GET `/api/services/status` | ✅ 后端 |
| agent provider 同步读/切（codex↔hermes） | GET/POST `/api/bg-agent/provider/route` | ✅ 后端 |
| summary provider 同步读/切（openrouter↔minimax） | GET/POST `/api/webinfer/summarizer/route` | ✅ 后端 |
| embedding provider 热切 | memory-store `/v1/settings/embedding`（webui 代理已有） | ✅ 后端 |

**前端现状（2026-08-16 核实）**：
- ✅ 设置弹窗（`settingsModal`）→ `servicesConfig` 面板**已有** 6 槽位表单 +
  provider 下拉（agent/embedding/summary）+ Save/Probe 按钮
  （`config_services.js` + `index.html:505-565`）
- ❌ **主界面完全无 provider 状态展示**——`live_ui.js`/主视图没有
  agent/summary/embedding 当前 provider 的指示元素
- ❌ `/api/services/status` 只被设置面板消费（badge 只在弹窗里）；主界面看不到
- ❌ 切换入口藏在设置弹窗的 **collapsed 面板**里（点 header 才展开）——普通用户
  不知道有热切换能力

**UI 组可做的暴露点（建议）**：
1. 主界面状态栏加「当前 provider」指示（agent=codex/hermes、summary=
   openrouter/minimax、embedding=…），轮询 `/api/services/status` 或
   `/api/bg-agent/provider/route` 刷新
2. 主界面加 provider 快捷切换（下拉），POST 到 route 端点，失败展示后端
   结构化错误（4xx/502 已有 body）
3. 设置弹窗 servicesConfig 面板可保持（高级配置位），主界面放精简版状态

## 4. 注意事项

- **OpenRouter 免费层限流**：50 requests/day（429 也计入）；免费模型还有上游
  共享池限流（`temporarily rate-limited upstream` 现象）——测试克制，详见
  `doc/specs/draft-provider-convergence.md` N8 附「OpenRouter 免费层限流与
  摘要频率」。
- **图片识别 skill 的定位**：它是独立工具（主对话侧用），**不是**子代理的
  看图方式；子代理看图 = 模型本身多模态（方案 B）。
- 本会话（主对话）继续负责后端/架构，UI 优化请 UI 组在独立对话推进，
  避免双端同时改 `services/webui/src/joy_interaction_webui/static/` 冲突。
