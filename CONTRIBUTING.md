# 贡献指南

## 开始之前

- 缺陷和小改动可直接提交 Issue 或 Pull Request。
- 模块边界、协议或主要功能变更，先在 Issue 说明目标与影响。
- 项目范围见 [AGENTS.md](AGENTS.md)，详细设计见 [文档索引](docs/index.md)。
- 项目采用 [PolyForm Noncommercial 1.0.0](LICENSE)。提交贡献前，请确认你有权按该许可授权自己的贡献。第三方代码保留原许可和必要声明。

## 开发环境

使用 Windows x64 和 Python 3.12 x64。在项目根目录执行：

```powershell
.\deploy.cmd -PrepareOnly
```

只修改文档或管理台时，可跳过唤醒模型下载：

```powershell
.\deploy.cmd -PrepareOnly -ServerOnly
```

脚本当前仅做静态检查，尚未执行部署验证。完整说明见 [部署指南](docs/部署指南.md)。

## 修改位置

| 内容 | 位置 |
| --- | --- |
| 语音设备、服务端和提供方适配 | `voice_platform/` |
| 网页管理台源码 | `admin_frontend/src/` |
| 随 Python 包分发的网页静态资源 | `voice_platform/admin_ui/` |
| 安装、部署和资源下载 | `scripts/` |
| 自动化测试 | `tests/` |
| 用户指南与设计记录 | `docs/` |

- Python 保留现有包结构。新功能按设备端、服务端、通用接入、部署与调试归属。
- 普通用户无需 Node.js。修改前端时，使用支持仓库锁定 Vite 版本的 Node.js 与 npm。
- 静态资源通过构建生成，不直接编辑压缩后的 JS 或 CSS。
- 不提交本地凭据、模型、数据库、日志或含个人信息的录音。
- 不在 Issue 或 PR 中粘贴真实 token 和 API Key。

## 验证命令

按改动范围选择检查，提交时记录实际执行结果。未执行的项目明确写明原因。

```powershell
# Python 测试；本地唤醒模型回归默认跳过。
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 修改管理台时需要重新构建，并提交生成资源。
cd admin_frontend
npm ci
npm run build
```

真实麦克风、Windows TTS 和 Vosk 回归步骤见 [详细运行指南](docs/运行指南.md#调试命令)。

## 提交约定

- 每个 PR 聚焦一项功能、修复或文档整理。
- 标题可使用 `feat:`、`fix:`、`docs:`、`refactor:` 等前缀。
- 描述问题、修改后的行为及验证结果。
- 功能或协议变化时更新使用文档与对应设计记录。
- 仅在实际验证后标记通过，不把静态检查写成部署或功能验收。
