# ASROK 配置面板使用指南

> ASROK = WebUI 里的 **ASR** 服务配置区（Services → ASR）。它控制浏览器麦克风音频送到哪个 ASR 引擎做转写。
> 2026-08-11 订正：用户只填**提供商 https 地址 + 模型 + 密钥**；`ws://` 是系统内部管道，用户无需、也不应填写。

## 1. 三个字段分别是什么意思？

ASR 与 LLM / TTS / summary 三个槽位**完全同构**：都是「API 地址(https) + 模型 + API 密钥」。

| 字段 | 作用 | 必填 | 示例 |
|---|---|---|---|
| **API 地址** | ASR 提供商的 **https Base URL**。 | 否（留空 = 用本地 in-proc 模型） | `https://api.siliconflow.cn/v1/audio/transcriptions` |
| **模型** | 提供商模型 ID（云端）；或本地 sherpa-onnx 模型目录（本地）。 | 否 | `FunAudioLLM/SenseVoiceSmall` |
| **API 密钥** | `Authorization: Bearer` 的 key。本地留空；云端必填。 | 否 | `sk-xxxxxxxxxxxxxxxx` |

**重要**：这里填的是**普通 https 地址**（和 LLM/TTS 的「API 地址」一样），**不是** `ws://`。`ws://` 是系统内部在 WebUI 与本地 ASR 引擎之间使用的管道，由系统自动管理，你不需要、也不应该手填。

## 2. 两种典型填法

### 2.1 本地 in-proc 模型（默认，最简单）

WebUI 进程内部直接加载 sherpa-onnx 模型，无需任何外部服务。

- **API 地址**：留空
- **模型**：`D:/AI/models/sherpa-onnx/models/asr/streaming-paraformer-bilingual-zh-en`（或留空用默认）
- **API 密钥**：留空

效果：浏览器音频在 webui 进程内转写，无额外端口依赖。

### 2.2 云端 ASR（SiliconFlow / 其他 OpenAI 兼容端点）

直接填提供商的 https 地址、模型、密钥即可。系统会自动接管「WebUI ↔ 本地桥 ↔ 云端」的内部转发。

- **API 地址**：`https://api.siliconflow.cn/v1/audio/transcriptions`
- **模型**：`FunAudioLLM/SenseVoiceSmall`
- **API 密钥**：`sk-你的密钥`

效果：浏览器音频 → webui →（系统管理的本地桥）→ SiliconFlow → 返回文本。用户全程只填 https + 模型 + 密钥。

> **内部桥说明（给运维/开发者，终端用户无需关心）**：WebUI 与 ASR 引擎之间实际用 websocket 通信。云端模式下，系统会把你的 https 配置交给本地桥 `services/asr/asr_adapter.py`（监听 `ws://127.0.0.1:8994/ws/asr`），由它把音频 POST 到云端。这个桥的地址是代码里的固定常量，不在面板中填写。在「自动生命周期管理」落地前，若需手动启动桥，命令见第 5 节。

## 3. 热重载机制（Save 后多久生效？）

点击 **Save** 后：

1. 前端把配置 PUT 到 `/api/services/config`。
2. 后端校验、落盘到 `config/services.json`（gitignored，重启保留）。
3. 后端标记 ASR 配置已更新。
4. **当前已连接的 ASR websocket 不会自动切换**。
5. **下一次浏览器新建 ASR 会话时**（刷新页面、或重新点击麦克风），读取最新 live config。

所以：改完配置需要**刷新浏览器**或**重新点击麦克风**，**不需要重启整个 `start-joyai.ps1` 服务栈**。

## 4. 状态徽章（OK / ERR / ...）

| 徽章 | 含义 |
|---|---|
| `OK` | `/api/services/status` 探活通过。 |
| `ERR` | 探活失败。常见：地址填错、云端不可达、密钥无效、本地桥未起。 |
| `...` | 正在探测。 |

## 5. 测试云端 ASR 的完整步骤（SiliconFlow SenseVoiceSmall）

> 当「自动生命周期管理」落地后，只需做第 4 步。当前若需手动拉起本地桥，按 1–3 步。

```powershell
# 1. （运维）在独立窗口启动本地桥，上游指向云端
cd D:\AI\workspace\JoyAI-VL-Interaction-main
$env:ASR_UPSTREAM_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
$env:ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"
$env:ASR_API_KEY = "sk-你的密钥"
D:\AI\envs\joyai-main\python.exe services\asr\asr_adapter.py --port 8994
```

```powershell
# 2. 确认桥健康
curl http://127.0.0.1:8994/health
```

```text
# 3. 在 WebUI 的 ASROK 面板填写（注意：是 https，不是 ws://）
API 地址: https://api.siliconflow.cn/v1/audio/transcriptions
模型:     FunAudioLLM/SenseVoiceSmall
API 密钥: sk-你的密钥
```

```text
# 4. 点击 Save，刷新浏览器，重新点击麦克风测试
```

## 6. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| 徽章 `ERR`，日志 "ASR url is not configured" | API 地址留空但程序试图走外部路径 | 确认是否想走本地 in-proc；若想外部，检查地址是否已保存。 |
| 徽章 `ERR`，日志 "ClientConnectorError" | 把 `ws://...` 填进了「API 地址」 | 改填提供商的 **https** 地址（如 `https://api.siliconflow.cn/v1/audio/transcriptions`）。`ws://` 是内部管道，不应手填。 |
| 外部 ASR 连不上，直接报错不 fallback | 默认行为：外部失败就报错，不静默切本地 | 如需临时 fallback，启动 webui 前设 `ASR_ALLOW_LOCAL_FAILOVER=1`。 |
| 改完配置没生效 | 当前 ASR websocket 仍在用旧配置 | 刷新浏览器或重新点击麦克风。 |

## 7. 与唤醒词（KWS）的关系

- ASROK 配置的是**对话 ASR**（你说的话转成文本送给 LLM）。
- 唤醒词 "bt" 由 KWS 模型处理，路径不同，不受 ASROK 直接影响。
- 当前 ASR promotion（KWS 漏唤醒时的本地 paraformer 兜底）会复用本地 in-proc ASR，因此 ASROK 里的「模型」路径也间接影响 promotion 效果。
