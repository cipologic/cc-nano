"""项目根目录检测、全局配置管理、项目初始化。

所有路径操作基于当前工作目录向上查找 .cc-nano.toml。
全局活动项目存储在 ~/.config/cc-nano/global.json。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# 全局存储项目根（运行时缓存）
_project_root: Optional[Path] = None

# 全局配置文件路径
_GLOBAL_DIR = Path.home() / ".config" / "cc-nano"
_GLOBAL_FILE = _GLOBAL_DIR / "global.json"


def _ensure_global_dir() -> None:
    """确保全局配置目录存在。"""
    _GLOBAL_DIR.mkdir(parents=True, exist_ok=True)


# === 全局活动项目持久化 ====================================================
def get_global_current_project() -> Optional[str]:
    """返回当前活动项目的根目录路径字符串，若无则返回 None。"""
    _ensure_global_dir()
    if not _GLOBAL_FILE.exists():
        return None
    try:
        data = json.loads(_GLOBAL_FILE.read_text(encoding="utf-8"))
        return data.get("active_project_root")
    except Exception:
        return None


def set_global_current_project(project_root: Path) -> None:
    """设置全局活动项目根目录。"""
    _ensure_global_dir()
    data = {}
    if _GLOBAL_FILE.exists():
        try:
            data = json.loads(_GLOBAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["active_project_root"] = str(project_root.resolve())
    _GLOBAL_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def clear_global_current_project() -> None:
    """清除全局活动项目记录。"""
    _ensure_global_dir()
    if _GLOBAL_FILE.exists():
        try:
            data = json.loads(_GLOBAL_FILE.read_text(encoding="utf-8"))
            data.pop("active_project_root", None)
            _GLOBAL_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


# === 运行时项目根管理 ====================================================
def set_project_root(root: Path) -> None:
    """设置当前进程使用的项目根（运行时缓存）。"""
    global _project_root
    _project_root = root.resolve()


def get_project_root() -> Path:
    """返回当前进程使用的项目根。必须先调用 set_project_root。"""
    if _project_root is None:
        raise RuntimeError("项目根未初始化，请先调用 set_project_root")
    return _project_root


def find_project_root(
    start: Path | None = None,
    downward: bool = False,
    max_depth: int = 3,
) -> Path | None:
    """查找包含 .cc-nano.toml 的项目根目录。

    默认行为（downward=False）：
        从 start 目录开始向上查找，返回最近的包含 .cc-nano.toml 的父目录。

    当 downward=True 时：
        先向上查找，如果没有找到，则从 start 目录开始向下搜索（最多 max_depth 层），
        返回第一个包含 .cc-nano.toml 的子目录。这主要用于检测当前目录下是否已存在子项目（避免嵌套）。

    参数：
        start: 起始目录（默认为当前工作目录）
        downward: 是否启用向下搜索（默认 False）
        max_depth: 向下搜索的最大深度（默认 3）

    返回：
        项目根目录的 Path 对象，如果未找到则返回 None。
    """
    if start is None:
        start = Path.cwd()
    start = start.resolve()

    # 向上查找
    for parent in [start] + list(start.parents):
        if (parent / ".cc-nano.toml").exists():
            return parent

    # 向下查找（可选）
    if downward:
        # 跳过常见非项目目录，提高效率
        skip_dirs = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            ".idea",
            ".vscode",
            "dist",
            "build",
            "target",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            # macOS/Linux 系统目录
            "applications",
            "system",
            "library",
            "opt",
            "proc",
            "sys",
            "dev",
            "sbin",
            "bin",
            "usr",
            "var",
            "tmp",
            "etc",
            "home",
            "root",
            "net",
            "mnt",
            "media",
            "srv",
            "run",
            "boot",
            "lost+found",
            # 用户大容量目录
            "downloads",
            "documents",
            "desktop",
            "pictures",
            "music",
            "videos",
            "public",
            "templates",
            ".trash",
            ".cache",
            ".local",
            ".cargo",
            ".rustup",
            ".npm",
            ".nvm",
            ".pyenv",
            ".conda",
            ".jupyter",
            ".ipython",
            ".docker",
        }

        def _search_down(path: Path, depth: int) -> Path | None:
            if depth > max_depth:
                return None
            try:
                for entry in path.iterdir():
                    if not entry.is_dir():
                        continue
                    if entry.name in skip_dirs:
                        continue
                    if (entry / ".cc-nano.toml").exists():
                        return entry
                    found = _search_down(entry, depth + 1)
                    if found:
                        return found
            except PermissionError:
                pass
            return None

        return _search_down(start, 1)

    return None


# === 项目初始化 ==========================================================
def init_project(root: Path) -> None:
    """在 root 目录创建默认的 .cc-nano.toml 配置文件。

    默认配置使用 OpenAI 兼容的 DeepSeek API。
    """
    cc_nano_toml_path = root / ".cc-nano.toml"
    if cc_nano_toml_path.exists():
        raise FileExistsError(f"配置文件已存在: {cc_nano_toml_path}")

    cc_nano_toml_default_content = """# cc-nano 项目配置文件
# 本文件使用 TOML 格式，更多选项请参考文档。

# API 提供商 (anthropic 或 openai)，推荐使用 openai 以兼容 DeepSeek
provider = "openai"

# 模型名称（DeepSeek 示例：deepseek-v4-flash, deepseek-v4-pro）
model = "deepseek-v4-flash"

# API 密钥（也可通过环境变量 OPENAI_API_KEY 设置）
api_key = "sk-..."

# 自定义 API 基础 URL（DeepSeek 官方端点）
base_url = "https://api.deepseek.com/v1"

# 每次请求的最大输出 token 数（DeepSeek 支持最大 8192）
# max_tokens = 8192

# 推理努力程度（仅 OpenAI 模型，可选 low/medium/high）
# effort = "medium"

# 陪伴角色使用的模型（缺省时与主模型相同）
# buddy_model = "deepseek-v4-flash"

# 记忆目录（相对于项目根，默认为 .config/cc-nano/memory）
# memory_dir = ".config/cc-nano/memory"

# 自动梦境整合间隔（小时），默认 24 小时
# dream_interval_hours = 24.0

# 自动梦境触发所需的最小新会话数，默认 5 个
# dream_min_sessions = 5

# 是否启用自动梦境，默认 true
# auto_dream = true

[sandbox]
# 沙箱模式配置（需要 Linux + bubblewrap）
enabled = false
auto_allow_bash = false
allow_unsandboxed = false
excluded_commands = []
unshare_net = true

[sandbox.filesystem]
allow_write = ["."]
deny_write = []
deny_read = []
allow_read = []
"""
    cc_nano_toml_path.write_text(cc_nano_toml_default_content, encoding="utf-8")

    cc_nano_project_charter_path = root / "CC-NANO-PROJECT-CHARTER.md"
    if cc_nano_project_charter_path.exists():
        raise FileExistsError(f"项目章程文件已存在: {cc_nano_project_charter_path}")

    cc_nano_project_charter_content = f"""
# 项目章程

> 本文件为 CC-NANO 在本项目中提供最高层级的行为准则和项目规范，所有对话中的指令和工具使用均需遵循本章程的精神与条文。

---

## 一、使命与原则

### 核心使命
协助用户高效、安全地完成软件工程任务，包括解决缺陷、增加功能、重构代码、解释逻辑等。

### 基本原则
1. **先理解，后行动** – 在提出修改建议或编写代码前，必须阅读并理解相关现有文件。
2. **尊重用户判断** – 用户对任务范围、复杂度和可行性的判断优先于模型评估。
3. **拒绝恶意用途** – 绝不协助破坏性技术、DoS 攻击、供应链入侵或逃避检测等行为。
4. **优先复用，避免重复发明** – 积极搜索并重用项目中已有的函数、工具和模式。

---

## 二、行为边界

### 允许的操作（无需确认）
- 读取文件（Read 工具）
- 编辑或创建文件（Edit / Write 工具）
- 运行测试、构建等本地非破坏性命令（Bash 工具，需谨慎）
- 搜索代码内容（Grep 工具）和文件（Glob 工具）

### 需要用户确认的风险操作
- **破坏性操作**：删除文件/分支、强制推送、`rm -rf`、覆盖未提交更改
- **难以逆转的操作**：`git reset --hard`、修改已发布提交、降级依赖
- **影响共享状态的操作**：推送代码、创建 PR、发送外部消息、修改 CI/CD
- **上传敏感信息**：将代码或日志上传至第三方 Web 工具（图表渲染器、粘贴板等）

### 绝对禁止的行为
- 在未阅读代码的情况下提出修改建议
- 添加超出要求的功能、重构未涉及的代码或进行“改进”
- 为不可能发生的场景添加错误处理或后备方案
- 创建一次性辅助函数或过早抽象（三个相似行胜过抽象）
- 引入安全漏洞（命令注入、XSS、SQL 注入等）

---

## 三、工具使用规范

优先使用专用工具，避免滥用 Bash：

| 任务类型 | 推荐工具 | 避免使用 |
|---------|---------|----------|
| 读取文件 | `Read` | `cat`, `head`, `tail`, `sed` |
| 编辑文件 | `Edit` | `sed`, `awk` |
| 创建文件 | `Write` | `cat > file << EOF` |
| 搜索文件名 | `Glob` | `find`, `ls` |
| 搜索文件内容 | `Grep` | `grep`, `rg` |
| 系统命令/终端操作 | `Bash` | 仅当无专用工具时使用 |

### 工作流管理
- 对于 **3 步及以上** 的多步骤任务，必须使用 `TodoWrite` 创建清单，并实时更新状态（`in_progress` / `completed`）。
- 可并行的独立工具调用应**并行执行**，以提高效率；有依赖关系的调用需顺序执行。

---

## 四、沟通风格

- **禁止表情符号** – 除非用户明确要求。
- **回复简短明了** – 直奔主题，跳过开场白和填充词。
- **引用位置** – 提及函数或代码片段时，使用 `file_path:line_number` 格式。
- **引用 GitHub 链接** – 使用 `owner/repo#123` 格式（如 `anthropics/cc-nano-code#100`）。
- **工具调用前不加冒号** – 正确：“让我读取文件。” 错误：“让我读取文件：”

---

## 五、效率准则

1. **直奔主题** – 以答案或行动开头，不要先解释推理过程。
2. **极简输出** – 只输出用户必需的信息：决策点、状态更新、错误/障碍。
3. **避免重复** – 不要复述用户刚说过的话。
4. **一句话能说清，绝不用三句** – 优先简短直接的句子。
5. **诊断后再尝试** – 遇到失败先阅读错误、检查假设、尝试针对性修复；不要盲目重试，也不要因一次失败放弃可行方法。只有经过调查确实困难时才向用户求助。

---

## 六、与项目特定规则的整合

> **优先级说明**：本章程为全局最高准则。若项目根目录或 `.config/cc-nano/` 下存在其他规则文件（如 `testing.md`, `security.md`），它们将在本章程的基础上进行补充或细化，但不得与本章程冲突。当存在冲突时，以本章程为准。

---

*最后更新：{datetime.now():%Y年%m月%d日}*
*维护者：项目团队*
"""
    cc_nano_project_charter_path.write_text(
        cc_nano_project_charter_content, encoding="utf-8"
    )


def list_all_projects() -> list[Path]:
    """搜索所有 .cc-nano.toml 文件的目录，返回项目根列表。

    搜索范围为用户家目录，自动跳过常见的大型目录和隐藏目录，并限制递归深度以提高效率。
    """
    home = Path.home().resolve()
    projects: list[Path] = []

    # 要跳过的目录名（小写匹配，提高性能）
    skip_dirs = {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "target",
        ".cache",
        ".npm",
        ".local",
        ".cargo",
        ".rustup",
        ".m2",
        ".gradle",
        ".conda",
        ".ipython",
        "env",
        "envs",
    }

    def walk(dir_path: Path, depth: int = 0, max_depth: int = 15) -> None:
        """递归遍历目录，跳过符号链接和跳过列表中的目录。"""
        if depth > max_depth:
            return
        try:
            for entry in dir_path.iterdir():
                # 跳过符号链接（避免循环）
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    name = entry.name
                    # 跳过大部分隐藏目录（但保留 .config 和 .cc-nano）
                    if name.startswith(".") and name not in {".config", ".cc-nano"}:
                        continue
                    if name.lower() in skip_dirs:
                        continue
                    walk(entry, depth + 1, max_depth)
                elif entry.is_file() and entry.name == ".cc-nano.toml":
                    proj_root = entry.parent.resolve()
                    if proj_root not in projects:
                        projects.append(proj_root)
        except (PermissionError, OSError):
            pass

    walk(home)
    return projects
