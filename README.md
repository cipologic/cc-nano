# cc-nano

**cc-nano** 是一个极简的 AI 编码助手，旨在帮助您在终端中快速完成软件工程任务。它支持文件编辑、搜索、Git 操作、代码审查、测试运行等常用开发流水线，并内置了有趣的“伙伴”（Buddy）系统和挂机冒险（Idle Adventure）迷你游戏。

## ✨ 主要特性

- **交互式 REPL** – 直接在终端中与 AI 对话，支持 `/` 斜杠命令和 `!` Shell 命令。
- **强大的工具集** – 文件读写、全局搜索（Glob）、正则搜索（Grep）、Bash 执行（支持沙箱隔离）。
- **记忆系统（KAIROS）** – 持久化记忆，跨会话回忆重要信息，支持每日日志和梦境整合。
- **计划模式** – 先探索、设计方案，获得用户批准后再实现代码。
- **团队协作模式** – 支持 Architect、TechLead、Implementer 等角色，自动拆分任务、生成计划和代码审查。
- **伙伴系统（Buddy）** – 孵化一只随机的 ASCII 艺术宠物，它会根据您的编码活动变化心情，并实时在终端右下角点评。
- **挂机冒险（Idle Adventure）** – 在内置的肉鸽式世界探索游戏中收集徽章，获得抽奖券，解锁稀有伙伴。
- **成本追踪** – 自动统计 API 调用 token 和费用（支持 DeepSeek V4 定价）。
- **沙箱隔离** – 使用 `bwrap` 将 Bash 命令运行在隔离环境中，提升安全性。

## 🚀 快速安装

```bash
# 从 PyPI 安装（推荐）
pip install cc-nano

# 或直接从源码安装
# 从gitee
git clone https://gitee.com/cipologic/cc-nano.git
# 或者从github
git clone https://github.com/cipologic/cc-nano.git
cd cc-nano
pip install -e .
```

## 🔧 首次使用

cc-nano 需要一个 API Key 来调用 LLM。您可以通过以下方式配置：

1. **环境变量**  
   ```bash
   export OPENAI_API_KEY="your-key-here"
   export OPENAI_BASE_URL="https://api.deepseek.com/v1"   # 可选
   ```

2. **项目配置文件**  
   在项目根目录创建 `.cc-nano.toml`。

3. **命令行参数**  
   ```bash
   cc-nano --api-key your-key --provider openai --model deepseek-v4-flash
   ```

## 💻 基本使用

启动交互式 REPL：

```bash
cc-nano
```

在提示符下，你可以：

- **直接提问**：例如 “解释 src/main.py 的功能”
- **使用专用工具**：系统会自动调用 `Read`、`Grep`、`Edit` 等工具完成任务
- **执行终端命令**：以 `!` 开头，例如 `! git status`
- **使用斜杠命令**：例如 `/help`, `/plan`, `/buddy`

## 📖 进阶学习

[《学废 cc-nano AI编程》](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Mzk0ODgxMTQ3Mg==&action=getalbum&album_id=4502433340980461571#wechat_redirect)
---

## 🧪 开发与贡献

欢迎提 Issue 和 Pull Request！

## 📜 许可证

本项目采用 **MIT 许可证**。详见 [LICENSE](LICENSE) 文件。