# openspeech

支持本地唤醒、实时语音对话和 MCP 工具接入的语音平台。源码公开，限非商业使用。

- 默认唤醒词：**小智小智**。唤醒词可配置，与项目名称分别维护。
- 默认使用 S2S 实时语音；也支持 ASR → LLM → TTS 串联模式。
- 设备端负责采音、唤醒和播放，服务端负责模型调用与会话管理。
- 通过 MCP 接入电脑工具和业务系统。工具服务可以独立于语音设备运行。
- 提供网页管理台，可配置模型、管理 MCP 服务器和查看对话日志。

## 当前支持

| 项目 | 状态 |
| --- | --- |
| Windows 设备端与服务端 | 已实现，主要部署路径 |
| S2S | 已适配 Qwen Realtime |
| 串联模式 | 已适配豆包 STT、兼容 Chat Completions 的 LLM、Windows TTS |
| MCP | 支持多个 Streamable HTTP 服务与工具白名单 |
| 树莓派、ESP32 原生设备端 | 待开发 |
| Linux、macOS、容器及系统服务部署 | 尚未提供部署脚本与验收结果 |

当前版本为 `0.1.0`。真实口音、现场噪声和模型账号权限需按部署环境验证。

## 快速开始

前置条件：Windows 10/11 x64、Python 3.12 x64、可访问依赖源和模型下载站点的网络。

1. 下载仓库 ZIP 并解压，或克隆项目：

   ```powershell
   git clone https://github.com/fyuanace/openspeech.git
   cd openspeech
   ```

2. 双击 `deploy.cmd`，或在项目根目录运行：

   ```powershell
   .\deploy.cmd
   ```

3. 等待依赖安装与唤醒模型下载完成。脚本随后在当前窗口启动服务端，请保留窗口。
4. 打开 [管理台](http://127.0.0.1:8765/admin)，选择接入平台并填写模型凭据。默认 S2S 使用千问 API Key；百炼接入还需业务空间 ID。
5. 配置完成后运行 `start-device.cmd`。连说“小智小智”与指令，例如“小智小智，介绍一下你自己”。

> 部署脚本已编写，仅做静态检查，尚未执行部署验证。首次部署仍需填写自己的云端凭据。

## 日常使用

| 操作 | 入口 |
| --- | --- |
| 启动服务端 | `start-server.cmd` |
| 启动语音设备端 | `start-device.cmd` |
| 配置模型、查看日志 | [管理台](http://127.0.0.1:8765/admin) |
| 停止语音设备端 | 托盘菜单 → 退出 |
| 停止服务端 | 服务端窗口按 `Ctrl+C` |

S2S 播报结束后可直接追问，默认等待 20 秒。说“结束对话”可请求回到等待唤醒。

## 项目结构

```text
openspeech/
├── voice_platform/        # Python 服务端、设备端与已构建的管理台
├── admin_frontend/        # Vue 管理台源码
├── scripts/               # 环境准备、部署与模型下载脚本
├── tests/                 # 协议、会话、音频和管理接口测试
├── docs/                  # 部署、使用、详细运行指南及设计文档
├── .github/               # Issue 与 Pull Request 模板
├── config.example.json    # 无密钥的示例配置
├── pyproject.toml         # Python 项目信息与依赖范围
├── requirements-lock.txt  # Windows / Python 3.12 依赖快照
├── deploy.cmd             # 一键准备环境并启动服务端
├── setup.cmd              # 仅准备环境，兼容原入口
├── start-server.cmd       # 日常启动服务端
└── start-device.cmd       # 日常启动语音设备端
```

运行时生成 `.venv/`、`config.local.json`、`models/`、`data/` 和 `logs/`。这些内容不进入 Git。

## 文档

| 文档 | 内容 |
| --- | --- |
| [部署指南](docs/部署指南.md) | 环境要求、一键部署、参数、更新与卸载 |
| [使用说明](docs/使用说明.md) | 首次配置、语音对话、日常启停 |
| [详细运行指南](docs/运行指南.md) | 全部配置、MCP、音频调试与模型扩展 |
| [文档总索引](docs/index.md) | 用户文档与两套设计文档库 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、代码位置与提交约定 |

## 许可证

采用 [PolyForm Noncommercial 1.0.0](LICENSE)：

- 允许非商业使用、学习、修改和分发。
- 分发时须附带许可文本或其链接，并保留 [NOTICE](NOTICE) 中的必要声明。
- 商业用途不在本许可授权范围内，需另行取得相关权利人的授权。

这是现成的软件非商业许可。项目属于源码可用项目，不使用 GPL，也不宣称符合 OSI 开源定义。该许可不强制公开修改后的源码，具体允许用途以协议原文为准。

第三方组件继续适用其各自许可。管理台声明见 [第三方依赖许可](voice_platform/admin_ui/THIRD_PARTY_LICENSES.md)，Vosk 模型许可随下载包提供。
