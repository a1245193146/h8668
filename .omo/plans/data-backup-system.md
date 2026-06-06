# 数据备份系统 - Work Plan

## TL;DR

> **Quick Summary**: 构建 Python 数据备份系统（调度器+执行器+校验器+告警器），支持 SQL Server/MySQL/SQLite/文件四种备份类型，含多盘轮询、MD5校验、链审计与熔断机制，配套 Apple 风格前端 Dashboard。
> 
> **Deliverables**:
> - Python 后端：4 模块（scheduler, executor, validator, alerter）+ config + main 入口
> - 前端 Dashboard：1 HTML + 1 JS + 1 CSS，Apple 风格可视化
> - 配置模板：config.example.json
> - 运行文档：启动说明
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 5 → Task 6 → Task 10 → Task 13 → Task 14 → F1-F4

---

## Context

### Original Request
构建一个完整的数据备份系统，含 Python 后端调度 + 前端可视化，支持 SQL Server / MySQL / SQLite / 文件备份，需要多盘动态轮询、MD5 校验、备份链完整性审计、熔断机制，前端需要 Apple 风格交互体验。

### Interview Summary
**Key Discussions**:
- 备份策略：周日全量 + 周一至周六增量/差异
- Linux MySQL：SSH + mysqldump + SFTP 拉回
- Windows SQL Server：subprocess + sqlcmd 本地执行
- 多盘轮询：扫描 >2TB 磁盘，空间不足自动切换
- 前端：Apple 风格（毛玻璃、大留白、微动效、深色模式）
- 测试：chrome-devtools 前端验证 + Agent QA 后端验证

**Research Findings**:
- 项目为全新绿地项目（仅有需求.txt）
- 已安装 8 个本地 Skills 覆盖所有技术领域
- 用户明确要求 anthropics/skills@frontend-design 做前端

### Metis Review
**Identified Gaps** (addressed):
- 前端服务方式 → Python http.server 静态服务
- 告警通道 → 日志 + 邮件 (SMTP)
- MySQL 备份方法 → mysqldump
- SSH 认证 → 密钥 (paramiko)
- 保留策略 → 30 天默认，可配置
- 恢复演练 → V1 仅日志提示，V2 实现自动恢复
- 并发防护 → PID 文件锁
- 时区处理 → 内部 UTC

---

## Work Objectives

### Core Objective
构建一个 Python 数据备份系统，包含调度器、执行器、校验器、告警器四个核心模块，支持 SQL Server/MySQL/SQLite/文件四种备份类型的全量+增量策略，配合 Apple 风格的前端可视化 Dashboard 展示备份状态。

### Concrete Deliverables
- `src/config.py` — 结构化配置管理
- `src/scheduler.py` — 调度器（时间判断 + 全量/增量决策）
- `src/executor.py` — 执行器（4 种备份类型实现）
- `src/validator.py` — 校验器（MD5 + 链审计 + 熔断）
- `src/alerter.py` — 告警器（日志 + 邮件）
- `src/main.py` — 主入口 + 编排逻辑
- `src/utils.py` — 共享工具（磁盘扫描、PID 锁、日志格式化）
- `src/run_server.py` — Dashboard HTTP 服务器
- `dashboard/index.html` + `dashboard/app.js` + `dashboard/style.css` — 前端
- `config.example.json` — 配置模板
- `install.bat` + `start.bat` + `start_dashboard.bat` — 启动脚本
- `vendor/` — 离线依赖 wheel 包
- `backup-system-release.zip` — 最终交付物

### Definition of Done
- [ ] `python src/main.py --dry-run` 可显示调度计划
- [ ] `python src/main.py --once` 可执行一次备份循环
- [ ] SQL Server 备份文件产出到指定磁盘 + MD5 文件伴生
- [ ] MySQL 备份通过 SSH 拉回 + MD5 验证通过
- [ ] 链断裂时自动触发全量（熔断日志可查）
- [ ] 前端打开显示备份状态图表，Apple 风格视觉效果
- [ ] 多盘轮询：磁盘空间不足时自动切换
- [ ] `install.bat` 可在无网络环境完成离线安装
- [ ] `backup-system-release.zip` 解压后即可部署运行

### Must Have
- 四模块严格分离（scheduler/executor/validator/alerter）
- MD5 流式计算（不加载全文件到内存）
- 备份链连续性审计 + 熔断降级
- 多盘 >2TB 动态轮询
- 前端 Apple 风格（毛玻璃、圆角、深色模式、微动效）
- PID 文件防并发
- 配置可热修改（无需重启即可读取新配置）
- 月度恢复演练日志提醒（V1：记录日志 + 发送邮件提醒管理员手动执行恢复验证）
- 使用 `uv` 管理 Python 虚拟环境和依赖（不污染系统 Python）
- 企业内网离线部署：所有依赖预下载打包，最终交付为可直接部署的压缩包
- 最终产物打包为 `backup-system-release.zip`，含所有源码、依赖、配置模板、启动脚本

### Must NOT Have (Guardrails)
- ❌ Django / Flask / FastAPI 等重型框架
- ❌ React / Vue / Angular 构建工具链
- ❌ 云存储集成 (S3, Azure Blob)
- ❌ 用户管理 / 登录认证系统
- ❌ 自动发现数据库（仅备份显式配置的目标）
- ❌ Dashboard 写操作（纯只读展示）
- ❌ 明文存储凭据
- ❌ 超过 3 个前端源文件
- ❌ 超过 4 种图表类型
- ❌ 自动恢复演练（V1 阶段仅日志提醒，不自动执行恢复）
- ❌ 直接 `pip install` 到系统 Python（必须通过 uv 虚拟环境）
- ❌ 运行时依赖互联网下载（所有依赖必须离线可用）
- ❌ 依赖需要编译的 C 扩展包（优先纯 Python 包，确保跨环境兼容）

### Defaults Applied (实现参数 — 可由用户覆盖)
> 以下数值为合理默认值，非需求文档明确指定。执行时按此实现，用户可通过 config.json 覆盖。
- **磁盘最小剩余空间阈值**: `min_free_gb=100`（低于100GB视为空间不足，切换下一磁盘）
- **前端告警列表上限**: 最多显示 20 条最近告警（防止 DOM 过长影响性能）
- **前端自动刷新间隔**: 60 秒（平衡实时性和服务器负载）
- **SSH 重试次数**: 3 次，间隔 30 秒
- **MD5 流式读取块大小**: 8192 bytes (8KB)
- **日志保留天数**: 30 天轮转
- **备份文件保留策略**: V1 不实现自动删除；由管理员通过操作系统计划任务或手动清理超过 30 天的备份文件。config.json 中预留 `retention_days` 字段供 V2 实现自动清理。

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO (greenfield project)
- **Automated tests**: None — Agent QA scenarios as primary verification
- **Framework**: None
- **Backend verification**: Agent 直接执行命令，检查文件输出、日志内容、MD5 一致性
- **Frontend verification**: chrome-devtools — 导航页面、断言 DOM 元素、截图验证视觉效果

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.

> **PYTHONPATH 规则**: 由于源码位于 `src/` 目录，所有 QA 命令必须在项目根目录下以 `set PYTHONPATH=src && python -c "..."` 形式运行（Windows），确保 `from config import ...` 等导入正常工作。或者等价地：`python -c "import sys; sys.path.insert(0,'src'); from config import load_config; ..."`

- **Backend/Python**: Use Bash — 运行 Python 脚本，检查输出文件、日志内容、exit code
- **Frontend/UI**: Use chrome-devtools — 打开 Dashboard，验证 DOM 结构、视觉效果、交互响应
- **Integration**: Use Bash + chrome-devtools — 端到端运行备份流程 + 前端展示验证

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — Start Immediately, 4 parallel):
├── Task 1: Project scaffolding + config module [quick]
├── Task 2: Disk scanner utility (multi-disk rotation) [unspecified-high]
├── Task 3: Frontend HTML skeleton (Apple-style base) [visual-engineering]
├── Task 4: Data models + JSON contract definition [quick]

Wave 2 (Core Modules — After Wave 1, 7 parallel, MAXIMUM THROUGHPUT):
├── Task 5: Scheduler module (decision logic) [unspecified-high]         depends: 1,4
├── Task 6: Executor — SQL Server backup [deep]                          depends: 1,2
├── Task 7: Executor — MySQL backup (SSH+SFTP) [deep]                    depends: 1,2
├── Task 8: Executor — SQLite + File backup [unspecified-high]           depends: 1,2
├── Task 9: Validator — MD5 checksum [unspecified-high]                  depends: 1,4
├── Task 11: Alerter module (log + email) [unspecified-high]             depends: 1,4
├── Task 13: Frontend — Dashboard charts + data rendering [visual-eng]   depends: 3,4

Wave 3 (Chain Audit + Frontend Polish — After Wave 2, 2 parallel):
├── Task 10: Validator — Chain audit + fuse mechanism [deep]             depends: 4,6,7,8
├── Task 14: Frontend — Apple polish (glassmorphism, animations) [vis]   depends: 13
  Note: Only 2 tasks — hard dependency on Wave 2 executor outputs forces this.

Wave 4 (Integration — After Wave 3, sequential forced by dependencies):
├── Task 12: Main orchestrator (wire all modules) [deep]                 depends: 5,9,10,11
├── Task 15: Integration test + end-to-end wiring [unspecified-high]     depends: 12,14
├── Task 16: Final packaging (offline deployment bundle) [unspecified-high] depends: 15
  Note: Sequential — T12→T15→T16 dependency chain.

Wave FINAL (4 parallel reviews, then user okay):
├── F1. Plan compliance audit (oracle)
├── F2. Code quality review (unspecified-high)
├── F3. Real manual QA via chrome-devtools (visual-engineering)
├── F4. Scope fidelity check (deep)
→ Present results → Get explicit user okay

Critical Path: Task 1 → Task 6 → Task 10 → Task 12 → Task 15 → Task 16 → F1-F4 → user okay
Parallel Speedup: ~60% faster than sequential (7 tasks in Wave 2)
Max Concurrent: 7 (Wave 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | — | 5,6,7,8,9,11,12,13 | 1 |
| 2 | — | 6,7,8 | 1 |
| 3 | — | 13,14 | 1 |
| 4 | — | 5,9,10,11,12,13 | 1 |
| 5 | 1,4 | 12 | 2 |
| 6 | 1,2 | 10 | 2 |
| 7 | 1,2 | 10 | 2 |
| 8 | 1,2 | 10 | 2 |
| 9 | 1,4 | 12 | 2 |
| 11 | 1,4 | 12 | 2 |
| 13 | 3,4 | 14 | 2 |
| 10 | 4,6,7,8 | 12 | 3 |
| 14 | 13 | 15 | 3 |
| 12 | 5,9,10,11 | 15 | 4 |
| 15 | 12,14 | 16 | 4 |
| 16 | 15 | F1-F4 | 4 |
| 15 | 12,14 | F1-F4 | 4 |

### Agent Dispatch Summary

- **Wave 1**: **4** — T1→`quick`, T2→`unspecified-high`, T3→`visual-engineering`, T4→`quick`
- **Wave 2**: **7** — T5→`unspecified-high`, T6→`deep`, T7→`deep`, T8→`unspecified-high`, T9→`unspecified-high`, T11→`unspecified-high`, T13→`visual-engineering`
- **Wave 3**: **2** — T10→`deep`, T14→`visual-engineering`
- **Wave 4**: **3** — T12→`deep`, T15→`unspecified-high`, T16→`unspecified-high` (sequential: T12→T15→T16)
- **FINAL**: **4** — F1→`oracle`, F2→`unspecified-high`, F3→`visual-engineering`, F4→`deep`

---

## TODOs

- [x] 1. Project Scaffolding + Config Module

  **What to do**:
  - 使用 `uv init` 初始化项目，创建 `pyproject.toml`
  - 添加依赖: `uv add paramiko` (SSH/SFTP)
  - 创建虚拟环境: `uv venv` → `.venv/` 目录
  - 创建项目目录结构：`src/config.py`, `src/scheduler.py`, `src/executor.py`, `src/validator.py`, `src/alerter.py`, `src/main.py`, `src/utils.py`, `dashboard/`
  - 实现 `config.py`：结构化存储数据库连接信息、备份路径、备份策略（全量/增量周期）、磁盘列表、邮件配置
  - 使用 Python dataclass 或 dict 结构，支持从 `config.json` 加载配置
  - 实现配置热加载：每次备份循环开始时重新读取配置文件
  - 创建 `config.example.json` 模板（含注释说明）
  - 创建 `start.bat` — Windows 启动脚本：激活 .venv + 运行 main.py
  - 在 `.gitignore` 中排除 `.venv/`, `logs/`, `*.bak`, `backup_status.json`

  **Must NOT do**:
  - 不使用 ORM 或重型配置库（如 dynaconf）
  - 不明文存储密码（使用环境变量或 keyring 占位）
  - 不使用 pip，必须用 uv 管理依赖
  - 不安装需要 C 编译的包（paramiko 是纯 Python + 依赖 cryptography wheel）

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 文件创建 + 简单数据结构定义，无复杂逻辑
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: Python 项目结构规范、类型注解

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: Tasks 5, 6, 7, 8, 9, 10, 11, 12
  - **Blocked By**: None

  **References**:
  - **Pattern References**: 无（绿地项目）
  - **External References**: Python dataclasses docs, json module

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Config loads successfully from JSON file
    Tool: Bash
    Preconditions: config.example.json exists with sample data
    Steps:
      1. Run: python -c "from config import load_config; cfg = load_config('config.example.json'); print(cfg)"
      2. Assert exit code == 0
      3. Assert output contains database connection keys
    Expected Result: Config object printed with all fields populated
    Failure Indicators: ImportError, KeyError, or non-zero exit
    Evidence: .omo/evidence/task-1-config-load.txt

  Scenario: Config rejects invalid JSON gracefully
    Tool: Bash
    Preconditions: Create malformed JSON file
    Steps:
      1. echo "{invalid" > /tmp/bad.json
      2. Run: python -c "from config import load_config; load_config('/tmp/bad.json')"
      3. Assert exit code != 0 or raises ConfigError
    Expected Result: Clear error message, no crash traceback
    Evidence: .omo/evidence/task-1-config-invalid.txt
  ```

  **Commit**: YES
  - Message: `feat(core): add project scaffolding and config module`
  - Files: `src/config.py`, `config.example.json`, `src/scheduler.py`, `src/executor.py`, `src/validator.py`, `src/alerter.py`, `src/main.py`, `src/utils.py`

- [x] 2. Disk Scanner Utility (Multi-Disk Rotation)

  **What to do**:
  - 在 `utils.py` 中实现 `scan_large_disks(min_size_tb=2)` 函数
  - 扫描 Windows 所有挂载盘符，筛选总容量 >2TB 的磁盘
  - 实现 `get_target_disk(min_free_gb=100)` — 按顺序返回第一个剩余空间充足的磁盘
  - 当前磁盘空间不足时，自动返回下一个可用磁盘
  - 所有磁盘满时返回 None（由调用者触发告警）
  - 使用 `shutil.disk_usage()` 或 `psutil`（如可用）
  - 实现 PID 文件锁：`acquire_lock()` / `release_lock()` 防止并发

  **Must NOT do**:
  - 不格式化或修改磁盘
  - 不处理网络共享盘（仅本地盘符）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 涉及系统级 API 调用，边界条件较多（磁盘满、权限问题）
  - **Skills**: [`python-best-practices`, `linux-server-expert`]
    - `python-best-practices`: 类型注解、错误处理模式
    - `linux-server-expert`: 磁盘管理概念（虽然目标是 Windows，但 skill 含存储逻辑）

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: Tasks 6, 7, 8, 12
  - **Blocked By**: None

  **References**:
  - **External References**: `shutil.disk_usage()` 文档, `os.listdir` 盘符枚举, Windows `win32api.GetLogicalDriveStrings`

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Scan detects available large disks
    Tool: Bash
    Preconditions: Windows machine with at least one drive
    Steps:
      1. Run: python -c "from utils import scan_large_disks; disks = scan_large_disks(min_size_tb=0.001); print(disks)"
      2. Assert output is a list with at least one drive letter
      3. Assert each entry has 'path', 'total', 'free' keys
    Expected Result: List of disk info dicts returned
    Failure Indicators: Empty list on machine with disks, or exception
    Evidence: .omo/evidence/task-2-disk-scan.txt

  Scenario: Get target disk skips full disks
    Tool: Bash
    Preconditions: Normal disk state
    Steps:
      1. Run: python -c "from utils import get_target_disk; d = get_target_disk(min_free_gb=999999); print(d)"
      2. Assert output is None (no disk has 999TB free)
      3. Run: python -c "from utils import get_target_disk; d = get_target_disk(min_free_gb=1); print(d)"
      4. Assert output is a valid path string
    Expected Result: Returns None when no space, valid path when space exists
    Evidence: .omo/evidence/task-2-disk-rotation.txt

  Scenario: PID lock prevents concurrent execution
    Tool: Bash
    Preconditions: No existing lock file
    Steps:
      1. Run: python -c "from utils import acquire_lock; print(acquire_lock())"
      2. Assert output is True
      3. Run (in parallel): python -c "from utils import acquire_lock; print(acquire_lock())"
      4. Assert second call returns False
    Expected Result: First acquires, second fails
    Evidence: .omo/evidence/task-2-pid-lock.txt
  ```

  **Commit**: YES (groups with Task 1)
  - Message: `feat(core): add project scaffolding and config module`
  - Files: `src/utils.py`

- [x] 3. Frontend HTML Skeleton (Apple-Style Base)

  **What to do**:
  - 创建 `dashboard/index.html` — 单页结构，引入本地 `dashboard/lib/chart.min.js`（离线 Chart.js）
  - 创建 `dashboard/style.css` — Apple 风格基础样式：
    - CSS 变量定义色板（深色模式优先）
    - 毛玻璃卡片：`backdrop-filter: blur(20px); background: rgba(255,255,255,0.05)`
    - 圆角：`border-radius: 20px`
    - 字体：`-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif`
    - 布局：CSS Grid，大量留白 `gap: 24px; padding: 32px`
  - 创建 `dashboard/app.js` — 空壳结构，预留 `loadData()`, `renderCharts()`, `init()` 函数
  - 下载 Chart.js minified 到 `dashboard/lib/chart.min.js`（从开发机预下载，离线可用）
  - 整体视觉：深灰背景 `#1c1c1e`，卡片微光边框，hover 时 scale(1.02) + box-shadow 扩展

  **Must NOT do**:
  - 不引入 npm / webpack / vite 构建工具
  - 不使用 UI 框架（Bootstrap, Tailwind 等）
  - 不实现数据加载逻辑（Task 13 负责）

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: 纯视觉/样式工作，需要设计审美判断
  - **Skills**: [`frontend-design`]
    - `frontend-design`: Anthropic 官方前端设计 skill，Apple 风格指导

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: Tasks 13, 14
  - **Blocked By**: None

  **References**:
  - **External References**: Chart.js 本地文件 (`dashboard/lib/chart.min.js`，开发时从官网预下载), Apple HIG 设计语言
  - **WHY Each Reference Matters**: Chart.js 是轻量级图表库（无构建依赖），Apple HIG 提供视觉风格参考

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Dashboard loads without errors
    Tool: chrome-devtools
    Preconditions: Python HTTP server running on port 8080 serving dashboard/
    Steps:
      1. Navigate to http://localhost:8080
      2. Wait for page load (timeout: 5s)
      3. Assert no console errors (filter: error type)
      4. Assert document.title contains "备份" or "Backup"
    Expected Result: Page loads cleanly, no JS errors
    Failure Indicators: 404, console errors, blank page
    Evidence: .omo/evidence/task-3-dashboard-load.png

  Scenario: Apple-style visual verification
    Tool: chrome-devtools
    Preconditions: Dashboard loaded
    Steps:
      1. Take screenshot of full page
      2. Assert body background-color is dark (#1c1c1e or similar)
      3. Assert .card elements have border-radius >= 16px
      4. Assert .card elements have backdrop-filter containing "blur"
      5. Assert font-family starts with "-apple-system" or "BlinkMacSystemFont"
    Expected Result: Dark theme, glassmorphism cards, Apple font stack
    Evidence: .omo/evidence/task-3-apple-style.png
  ```

  **Commit**: NO (groups with Task 14)

- [x] 4. Data Models + JSON Contract Definition

  **What to do**:
  - 定义 `backup_status.json` schema（后端写入、前端读取的数据契约）：
    ```json
    {
      "last_updated": "ISO8601",
      "disks": [{"path": "D:", "total_gb": 4000, "free_gb": 2100}],
      "jobs": [{
        "name": "sqlserver_main",
        "type": "sqlserver|mysql|sqlite|file",
        "last_full": "ISO8601",
        "last_incremental": "ISO8601",
        "chain_status": "intact|broken|unknown",
        "last_result": "success|failed|skipped",
        "file_path": "D:/backups/...",
        "file_size_mb": 1234,
        "md5_verified": true
      }],
      "alerts": [{"time": "ISO8601", "level": "info|warn|error", "message": "..."}]
    }
    ```
  - 在 `utils.py` 中实现 `write_status(data, path)` 和 `read_status(path)` 函数
  - 创建 `sample_status.json` 示例文件供前端开发使用

  **Must NOT do**:
  - 不使用数据库存储状态（JSON 文件即可）
  - 不引入 Pydantic 或 marshmallow（纯 dict + json 模块）

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 定义数据结构 + 写简单读写函数
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: 类型注解、JSON 处理模式

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: Tasks 5, 9, 10, 11, 12, 13
  - **Blocked By**: None

  **References**:
  - **External References**: JSON Schema spec, Python json module docs

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Write and read status roundtrip
    Tool: Bash
    Preconditions: utils.py with write_status/read_status implemented
    Steps:
      1. Run: python -c "from utils import write_status, read_status; import json; data = {'last_updated':'2024-01-01T00:00:00','disks':[],'jobs':[],'alerts':[]}; write_status(data, '/tmp/test_status.json'); result = read_status('/tmp/test_status.json'); assert result == data; print('OK')"
      2. Assert output contains "OK"
      3. Assert /tmp/test_status.json file exists and is valid JSON
    Expected Result: Data roundtrips without loss
    Failure Indicators: AssertionError, file not created
    Evidence: .omo/evidence/task-4-status-roundtrip.txt

  Scenario: sample_status.json is valid and complete
    Tool: Bash
    Preconditions: sample_status.json created
    Steps:
      1. Run: python -c "import json; d = json.load(open('sample_status.json')); assert 'disks' in d; assert 'jobs' in d; assert 'alerts' in d; assert len(d['jobs']) > 0; print('Schema valid')"
      2. Assert output is "Schema valid"
    Expected Result: Sample file has all required top-level keys with example data
    Evidence: .omo/evidence/task-4-sample-valid.txt
  ```

  **Commit**: YES (groups with Task 1)
  - Message: `feat(core): add project scaffolding and config module`
  - Files: `sample_status.json`

- [x] 5. Scheduler Module (Decision Logic)

  **What to do**:
  - 实现 `scheduler.py`：
    - `should_run_full_backup(job_config, history)` — 判断今天是否应执行全量（周日 or 链断裂 or 首次运行）
    - `should_run_incremental(job_config, history)` — 判断今天是否应执行增量（周一至周六且链完整）
    - `get_today_schedule(config)` — 返回今日所有待执行备份任务列表
    - `run_schedule_loop(config)` — 主循环：每分钟检查时间，到点触发
  - 调度逻辑：
    - 内部使用 UTC 时间
    - 支持配置中指定每个任务的执行时间（如 "02:00"）
    - 首次运行（无历史记录）→ 强制全量
    - 周日 → 全量
    - 周一至周六 → 增量/差异（前提：链完整）
  - 调度器只决策，不执行（返回 task list 由 main.py 分发给 executor）

  **Must NOT do**:
  - 不直接调用备份命令（仅返回决策结果）
  - 不使用第三方调度库（APScheduler 等）
  - 不使用 cron（纯 Python 时间判断）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 时间逻辑 + 状态判断，边界条件较多（跨日、首次、链断裂）
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: 控制流、日期处理模式

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 7, 8)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 1, 4

  **References**:
  - **Pattern References**: `config.py` 中的 job 配置结构（Task 1 产出）
  - **API References**: `backup_status.json` 的 jobs[].last_full / last_incremental 字段（Task 4 定义）
  - **External References**: Python `datetime` 模块, `time.sleep`

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Sunday triggers full backup
    Tool: Bash
    Preconditions: scheduler.py implemented, mock history with last_full = 7 days ago
    Steps:
      1. Run: python -c "from scheduler import should_run_full_backup; from datetime import datetime; result = should_run_full_backup({'schedule':'weekly_full'}, {'last_full':'2024-01-07'}); print(result)"
         (with system date mocked to Sunday or testing logic directly)
      2. Assert output is True
    Expected Result: Returns True for full backup on Sunday logic
    Evidence: .omo/evidence/task-5-sunday-full.txt

  Scenario: First run with no history forces full backup
    Tool: Bash
    Preconditions: Empty history (no previous backups)
    Steps:
      1. Run: python -c "from scheduler import should_run_full_backup; result = should_run_full_backup({'schedule':'weekly_full'}, {}); print(result)"
      2. Assert output is True
    Expected Result: No history = force full backup
    Evidence: .omo/evidence/task-5-first-run.txt

  Scenario: Weekday with intact chain triggers incremental
    Tool: Bash
    Preconditions: History shows full backup exists this week, chain intact
    Steps:
      1. Run test with mocked weekday + intact chain status
      2. Assert should_run_incremental returns True
      3. Assert should_run_full_backup returns False
    Expected Result: Incremental selected, not full
    Evidence: .omo/evidence/task-5-weekday-incr.txt
  ```

  **Commit**: YES
  - Message: `feat(scheduler): implement backup schedule decision logic`
  - Files: `src/scheduler.py`

- [x] 6. Executor — SQL Server Backup

  **What to do**:
  - 在 `executor.py` 中实现 `backup_sqlserver(job_config, target_disk)`:
    - 使用 `subprocess.run()` 调用 `sqlcmd` 执行 T-SQL BACKUP 命令
    - 全量: `BACKUP DATABASE [db] TO DISK='path' WITH INIT, COMPRESSION`
    - 差异: `BACKUP DATABASE [db] TO DISK='path' WITH DIFFERENTIAL, COMPRESSION`
    - 文件命名: `{job_name}_{date}_{full|diff}.bak`（与 Task 10 链审计命名规则一致）
    - 同时生成 `.md5` 伴生文件（流式计算 MD5 写入）
    - 目标路径由 `get_target_disk()` 提供（Task 2）
    - 返回 `BackupResult` dict: `{success, file_path, file_size, md5, duration, error_msg}`
  - 错误处理：
    - sqlcmd 返回非0 → 记录 stderr → 返回 failed
    - 文件不存在/大小为0 → 标记失败
    - 磁盘空间不足 → 调用 `get_target_disk()` 重试一次

  **Must NOT do**:
  - 不使用 pyodbc 或 pymssql 连接（直接 subprocess + sqlcmd）
  - 不在 executor 中做调度决策
  - 不整文件加载计算 MD5（必须流式 chunk 读取）

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: subprocess 调用 + 错误处理 + 重试逻辑 + 流式 MD5，复杂度高
  - **Skills**: [`python-best-practices`, `mssql`, `database-backups`]
    - `python-best-practices`: subprocess 调用模式、错误处理
    - `mssql`: SQL Server 备份 T-SQL 语法、sqlcmd 用法
    - `database-backups`: 备份策略（全量/差异/日志）

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 7, 8)
  - **Blocks**: Tasks 10, 12
  - **Blocked By**: Tasks 1, 2

  **References**:
  - **API References**: `utils.py:get_target_disk()` (Task 2), `config.py` job 结构 (Task 1)
  - **External References**:
    - `sqlcmd` 命令行参考: `sqlcmd -S server -U user -P pass -Q "BACKUP DATABASE..."`
    - T-SQL BACKUP 语法: `BACKUP DATABASE [db] TO DISK=N'path' WITH DIFFERENTIAL, COMPRESSION`
    - Python `subprocess.run` with `capture_output=True, text=True`
    - `hashlib.md5()` 流式更新: `md5.update(chunk)` in 8KB blocks

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: SQL Server full backup produces .bak + .md5
    Tool: Bash
    Preconditions: SQL Server accessible, sqlcmd available, config with valid connection
    Steps:
      1. Run: python -c "from executor import backup_sqlserver; result = backup_sqlserver({'type':'sqlserver','server':'localhost','database':'testdb','auth':{'user':'sa','password':'...'}}, 'D:/backups'); print(result)"
      2. Assert result['success'] == True
      3. Assert file exists at result['file_path']
      4. Assert file size > 0
      5. Assert .md5 file exists alongside .bak file
      6. Verify MD5 in .md5 matches actual file hash
    Expected Result: Backup file + MD5 file created, result dict shows success
    Failure Indicators: success=False, file missing, MD5 mismatch
    Evidence: .omo/evidence/task-6-sqlserver-full.txt

  Scenario: SQL Server backup handles connection failure gracefully
    Tool: Bash
    Preconditions: Invalid server connection in config
    Steps:
      1. Run backup_sqlserver with server='nonexistent_server'
      2. Assert result['success'] == False
      3. Assert result['error_msg'] contains meaningful error text
      4. Assert no partial .bak file left on disk
    Expected Result: Clean failure with error message, no orphan files
    Evidence: .omo/evidence/task-6-sqlserver-fail.txt
  ```

  **Commit**: YES
  - Message: `feat(executor): implement SQL Server backup with MD5`
  - Files: `src/executor.py`

- [x] 7. Executor — MySQL Backup (SSH + SFTP)

  **What to do**:
  - 在 `executor.py` 中实现 `backup_mysql(job_config, target_disk)`:
    - 使用 paramiko 建立 SSH 连接（密钥认证）
    - 远程执行 `mysqldump --single-transaction --routines --triggers {db} | gzip > /tmp/{job_name}_{date}_{full|incr}.sql.gz`
    - 远程生成 MD5: `md5sum /tmp/{job_name}_{date}_{full|incr}.sql.gz > /tmp/{job_name}_{date}_{full|incr}.sql.gz.md5`
    - SFTP 拉回文件到 Windows target_disk
    - 本地重新计算 MD5，对比远程 MD5
    - MD5 一致 → 成功; 不一致 → 告警 + 重试（最多3次）
    - 传输完成后清理远程临时文件
    - 返回 `BackupResult` dict
  - 错误处理：
    - SSH 连接失败 → 重试3次，间隔30秒
    - SFTP 传输中断 → 删除本地残文件 + 重试
    - 远程 mysqldump 失败 → 捕获 stderr → 返回 failed

  **Must NOT do**:
  - 不使用 fabric 库（直接 paramiko）
  - 不在 Windows 本地安装 mysqldump
  - 不存储密码明文（SSH 密钥路径从 config 读取）

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: SSH + SFTP + 远程命令 + MD5 对比 + 重试逻辑，多层错误处理
  - **Skills**: [`python-best-practices`, `mysql`, `ssh-remote`, `database-backups`]
    - `python-best-practices`: 错误处理、重试模式
    - `mysql`: mysqldump 参数、事务一致性选项
    - `ssh-remote`: paramiko SSH/SFTP 连接模式
    - `database-backups`: 远程备份拉取策略

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 8)
  - **Blocks**: Tasks 10, 12
  - **Blocked By**: Tasks 1, 2

  **References**:
  - **API References**: `utils.py:get_target_disk()` (Task 2), config SSH 配置字段 (Task 1)
  - **External References**:
    - paramiko docs: `SSHClient.connect()`, `SFTPClient.get()`
    - mysqldump: `--single-transaction --routines --triggers --set-gtid-purged=OFF`
    - Python hashlib 流式 MD5: `for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)`

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: MySQL backup via SSH produces compressed file + MD5 match
    Tool: Bash
    Preconditions: Linux server with MySQL accessible via SSH key, config properly set
    Steps:
      1. Run: python -c "from executor import backup_mysql; result = backup_mysql({'type':'mysql','ssh_host':'192.168.1.10','ssh_key':'~/.ssh/id_rsa','database':'appdb','mysql_user':'backup_user'}, 'D:/backups'); print(result)"
      2. Assert result['success'] == True
      3. Assert local file exists and ends with .sql.gz
      4. Assert .md5 file exists
      5. Assert local MD5 matches content of .md5 file
    Expected Result: File pulled successfully, MD5 verified
    Failure Indicators: success=False, MD5 mismatch, file missing
    Evidence: .omo/evidence/task-7-mysql-backup.txt

  Scenario: SSH connection failure triggers retry and clean error
    Tool: Bash
    Preconditions: Invalid SSH host in config
    Steps:
      1. Run backup_mysql with ssh_host='10.255.255.1' (unreachable)
      2. Assert function retries (check log for "retry" messages)
      3. Assert result['success'] == False after retries exhausted
      4. Assert result['error_msg'] mentions connection
      5. Assert no partial files left in target directory
    Expected Result: Graceful failure after 3 retries, no orphan files
    Evidence: .omo/evidence/task-7-mysql-ssh-fail.txt
  ```

  **Commit**: YES (groups with Task 6)
  - Message: `feat(executor): implement SQL Server backup with MD5`
  - Files: `src/executor.py`

- [x] 8. Executor — SQLite + File Backup

  **What to do**:
  - 在 `executor.py` 中实现 `backup_sqlite(job_config, target_disk)`:
    - 使用 `sqlite3` 模块的 `.backup()` API 做在线热备份
    - 或 WAL checkpoint + 文件复制（`PRAGMA wal_checkpoint(TRUNCATE)`）
    - 压缩为 `.db.gz`
    - 生成 MD5 伴生文件
    - 文件命名: `{job_name}_{date}_{full|incr}.db.gz`（与 Task 10 链审计命名规则一致）
  - 在 `executor.py` 中实现 `backup_files(job_config, target_disk)`:
    - 从配置读取源目录列表
    - 全量: 使用 `zipfile` 压缩整个目录，命名: `{job_name}_{date}_full.zip`
    - 增量: 基于修改时间 `os.stat().st_mtime`，仅复制比上次备份更新的文件，命名: `{job_name}_{date}_incr.zip`
    - 生成 manifest 文件记录本次备份包含的文件列表
    - 生成 MD5
  - 返回 `BackupResult` dict

  **Must NOT do**:
  - 不使用 rsync（纯 Python 实现）
  - 不备份系统目录或临时文件
  - SQLite 备份不直接 copy 数据库文件（必须用 backup API 或 WAL checkpoint）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 两种备份类型实现 + 增量文件检测逻辑
  - **Skills**: [`python-best-practices`, `database-backups`]
    - `python-best-practices`: 文件操作、压缩模式
    - `database-backups`: SQLite 备份最佳实践

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7)
  - **Blocks**: Tasks 10, 12
  - **Blocked By**: Tasks 1, 2

  **References**:
  - **External References**:
    - Python `sqlite3.Connection.backup()` docs
    - `PRAGMA wal_checkpoint(TRUNCATE)` SQLite docs
    - `zipfile.ZipFile` with compression
    - `os.walk()` + `os.stat().st_mtime` for incremental detection

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: SQLite backup creates compressed copy with MD5
    Tool: Bash
    Preconditions: A test SQLite database file exists
    Steps:
      1. Create test db: python -c "import sqlite3; c=sqlite3.connect('/tmp/test.db'); c.execute('CREATE TABLE t(id INT)'); c.execute('INSERT INTO t VALUES(1)'); c.commit(); c.close()"
      2. Run: python -c "from executor import backup_sqlite; result = backup_sqlite({'type':'sqlite','db_path':'/tmp/test.db','name':'testdb'}, 'D:/backups'); print(result)"
      3. Assert result['success'] == True
      4. Assert .db.gz file exists
      5. Assert .md5 file exists
    Expected Result: Compressed backup + MD5 created
    Evidence: .omo/evidence/task-8-sqlite-backup.txt

  Scenario: File backup incremental detects only changed files
    Tool: Bash
    Preconditions: Source directory with files of varying mtimes
    Steps:
      1. Create test directory with 3 files
      2. Run full backup (first run)
      3. Modify 1 file
      4. Run incremental backup
      5. Assert incremental contains only the 1 modified file
    Expected Result: Incremental backup smaller than full, contains only changes
    Evidence: .omo/evidence/task-8-file-incremental.txt
  ```

  **Commit**: YES (groups with Tasks 6, 7)
  - Message: `feat(executor): add SQLite and file backup executors`
  - Files: `src/executor.py`

- [x] 9. Validator — MD5 Checksum Module

  **What to do**:
  - 在 `validator.py` 中实现 `verify_md5(file_path, expected_md5_path)`:
    - 流式读取文件（8KB chunks），计算 MD5
    - 读取 `.md5` 伴生文件中的期望值
    - 对比：一致返回 True，不一致返回 False + 详细错误信息
  - 实现 `compute_md5_stream(file_path)` — 独立的流式 MD5 计算函数
  - 实现 `validate_backup_file(backup_result)`:
    - 检查文件存在性
    - 检查文件大小 > 0
    - 执行 MD5 校验
    - 返回 `ValidationResult`: `{valid, file_path, expected_md5, actual_md5, error}`
  - 性能要求：10GB 文件不应 OOM（流式处理）

  **Must NOT do**:
  - 不整文件 `open().read()` 加载到内存
  - 不使用 SHA256（需求明确要求 MD5）
  - 不跳过大文件校验

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 流式处理 + 边界条件（大文件、权限问题、文件锁）
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: 文件 I/O 模式、内存管理

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7, 8, 11, 13)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 1, 4

  **References**:
  - **API References**: `backup_status.json` 中的 md5_verified 字段 (Task 4)
  - **External References**: `hashlib.md5()`, `iter(lambda: f.read(8192), b'')` 流式读取模式

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: MD5 validation passes for intact file
    Tool: Bash
    Preconditions: A test file with matching .md5 sidecar
    Steps:
      1. Create test file: echo "hello backup" > /tmp/test_backup.bak
      2. Generate MD5: python -c "import hashlib; print(hashlib.md5(open('/tmp/test_backup.bak','rb').read()).hexdigest())" > /tmp/test_backup.bak.md5
      3. Run: python -c "from validator import verify_md5; result = verify_md5('/tmp/test_backup.bak', '/tmp/test_backup.bak.md5'); print(result)"
      4. Assert result is True
    Expected Result: Validation passes, returns True
    Evidence: .omo/evidence/task-9-md5-pass.txt

  Scenario: MD5 validation fails for corrupted file
    Tool: Bash
    Preconditions: File with wrong .md5 sidecar
    Steps:
      1. Create test file and mismatched .md5
      2. echo "wrong_hash_value" > /tmp/test_backup.bak.md5
      3. Run: python -c "from validator import verify_md5; result = verify_md5('/tmp/test_backup.bak', '/tmp/test_backup.bak.md5'); print(result)"
      4. Assert result is False
    Expected Result: Validation fails, returns False with mismatch details
    Evidence: .omo/evidence/task-9-md5-fail.txt

  Scenario: Stream hashing does not OOM on large simulated file
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: python -c "from validator import compute_md5_stream; import os, tempfile; f=tempfile.NamedTemporaryFile(delete=False); f.write(b'x'*100_000_000); f.close(); h=compute_md5_stream(f.name); os.unlink(f.name); print(h)"
      2. Assert process RSS stays under 50MB (check via resource module or observation)
      3. Assert hash is returned (32-char hex string)
    Expected Result: 100MB file hashed without memory spike
    Evidence: .omo/evidence/task-9-md5-memory.txt
  ```

  **Commit**: YES
  - Message: `feat(validator): implement MD5 checksum verification`
  - Files: `src/validator.py`

- [x] 10. Validator — Chain Audit + Fuse Mechanism

  **What to do**:
  - 在 `validator.py` 中实现 `audit_backup_chain(job_config, backup_dir)`:
    - 扫描备份目录，按日期排序找到本周的备份文件
    - 检查：本周日全备文件是否存在且大小>0
    - 检查：从周一到昨天的每个增量文件是否存在且大小>0
    - 返回 `ChainAuditResult`: `{intact, missing_files, last_valid_date, recommendation}`
  - 实现 `fuse_check(audit_result)`:
    - 如果 chain 断裂（中间缺失文件）→ 返回 `"force_full"` 建议
    - 如果全备缺失 → 返回 `"force_full"`
    - 如果链完整 → 返回 `"proceed_incremental"`
  - 实现 `get_chain_files(job_name, backup_dir, week_start_date, backup_type)`:
    - 根据 backup_type 选择文件名规则：
      - sqlserver: `{job_name}_{date}_{full|diff}.bak`
      - mysql: `{job_name}_{date}_{full|incr}.sql.gz`
      - sqlite: `{job_name}_{date}_{full|incr}.db.gz`
      - file: `{job_name}_{date}_{full|incr}.zip`
    - 返回有序文件列表 + 缺失日期列表
  - 熔断逻辑集成：scheduler 调用 fuse_check，如果返回 force_full 则覆盖今日决策

  **Must NOT do**:
  - 不删除或移动现有备份文件
  - 不自动修复链（只诊断 + 建议）
  - 不跨周检查（每周独立链）

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 文件系统扫描 + 日期逻辑 + 多种断裂场景判断，业务逻辑复杂
  - **Skills**: [`python-best-practices`, `database-backups`]
    - `python-best-practices`: 日期处理、文件遍历
    - `database-backups`: 备份链概念、增量依赖关系

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 14)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 4, 6, 7, 8 (needs to understand file naming from executors)

  **References**:
  - **Pattern References**: executor 的文件命名规则 (Tasks 6,7,8):
    - sqlserver: `{job_name}_{date}_{full|diff}.bak`
    - mysql: `{job_name}_{date}_{full|incr}.sql.gz`
    - sqlite: `{job_name}_{date}_{full|incr}.db.gz`
    - file: `{job_name}_{date}_{full|incr}.zip`
  - **API References**: `backup_status.json:jobs[].chain_status` (Task 4)
  - **External References**: Python `datetime.isocalendar()` for week logic, `pathlib.Path.glob()`

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Intact chain detected correctly
    Tool: Bash
    Preconditions: Simulated backup directory with complete week of files
    Steps:
      1. Create dir with: job_20240107_full.bak, job_20240108_diff.bak, job_20240109_diff.bak (all >0 bytes)
      2. Run: python -c "from validator import audit_backup_chain; r = audit_backup_chain({'name':'job'}, '/tmp/backups'); print(r)"
      3. Assert r['intact'] == True
      4. Assert r['missing_files'] == []
    Expected Result: Chain reported as intact
    Evidence: .omo/evidence/task-10-chain-intact.txt

  Scenario: Missing incremental triggers fuse
    Tool: Bash
    Preconditions: Backup directory with gap (Tuesday missing)
    Steps:
      1. Create: job_20240107_full.bak, job_20240108_diff.bak, (skip Tue), job_20240110_diff.bak
      2. Run audit_backup_chain
      3. Assert r['intact'] == False
      4. Assert '20240109' in r['missing_files']
      5. Run: python -c "from validator import fuse_check; print(fuse_check(r))"
      6. Assert output == "force_full"
    Expected Result: Gap detected, fuse recommends full backup
    Evidence: .omo/evidence/task-10-chain-broken.txt

  Scenario: Missing full backup triggers immediate fuse
    Tool: Bash
    Preconditions: Backup directory with only incremental files (no Sunday full)
    Steps:
      1. Create only: job_20240108_diff.bak, job_20240109_diff.bak
      2. Run audit + fuse_check
      3. Assert fuse returns "force_full"
    Expected Result: No full backup = force full immediately
    Evidence: .omo/evidence/task-10-no-full.txt
  ```

  **Commit**: YES (groups with Task 9)
  - Message: `feat(validator): implement MD5 checksum verification`
  - Files: `src/validator.py`

- [x] 11. Alerter Module (Log + Email)

  **What to do**:
  - 实现 `alerter.py`:
    - `setup_logger(config)` — 配置 Python logging，输出到文件 + console
    - `send_alert(level, message, config)`:
      - level: info / warn / error / critical
      - 写入日志文件（始终）
      - error/critical 级别 → 发送邮件 (SMTP)
    - `send_email(subject, body, config)`:
      - 使用 `smtplib` + `email.mime`
      - 支持 TLS
      - 连接失败时 → 记录到日志（不因邮件失败而中断备份流程）
    - `alert_backup_failed(job_name, error_msg, config)` — 预设模板
    - `alert_md5_mismatch(job_name, expected, actual, config)` — 预设模板
    - `alert_chain_broken(job_name, missing_files, config)` — 预设模板
    - `alert_disk_full(disk_path, free_gb, config)` — 预设模板
  - 日志格式: `[2024-01-07 02:30:15 UTC] [ERROR] [sqlserver_main] MD5 mismatch: expected abc123, got def456`
  - 日志文件轮转: 按天轮转，保留30天

  **Must NOT do**:
  - 不引入第三方邮件库（纯 smtplib）
  - 不因邮件发送失败而中断主流程
  - 不发送 info 级别邮件（只 error/critical）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: SMTP 连接 + logging 配置 + 错误隔离逻辑
  - **Skills**: [`python-best-practices`, `monitoring-and-alerting`]
    - `python-best-practices`: logging 模块配置模式
    - `monitoring-and-alerting`: 告警分级策略、通知模式

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7, 8, 9, 13)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 1, 4

  **References**:
  - **API References**: config 中的 email 配置字段 (Task 1), `backup_status.json:alerts[]` (Task 4)
  - **External References**:
    - Python `logging.handlers.TimedRotatingFileHandler` (按天轮转)
    - `smtplib.SMTP_SSL` / `SMTP.starttls()`
    - `email.mime.text.MIMEText` 构建邮件

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Alert writes to log file with correct format
    Tool: Bash
    Preconditions: alerter.py implemented, config with log path
    Steps:
      1. Run: python -c "from alerter import setup_logger, send_alert; setup_logger({'log_path':'./test.log'}); send_alert('error', 'Test error message', {'log_path':'./test.log'})"
      2. Assert ./test.log exists
      3. Assert log contains "[ERROR]" and "Test error message"
      4. Assert log line has UTC timestamp format
    Expected Result: Log file created with properly formatted error entry
    Failure Indicators: File not created, wrong format, no timestamp
    Evidence: .omo/evidence/task-11-log-write.txt

  Scenario: Email send failure does not crash the process
    Tool: Bash
    Preconditions: Invalid SMTP config (unreachable host)
    Steps:
      1. Run: python -c "from alerter import send_alert; send_alert('critical', 'System down', {'log_path':'./test.log', 'email':{'smtp_host':'invalid.host','port':587,'from':'a@b.c','to':'x@y.z'}})"
      2. Assert exit code == 0 (no crash)
      3. Assert log file contains the alert message AND an email failure warning
    Expected Result: Alert logged successfully despite email failure
    Evidence: .omo/evidence/task-11-email-fail-safe.txt
  ```

  **Commit**: YES
  - Message: `feat(alerter): implement logging and email notification system`
  - Files: `src/alerter.py`

- [x] 12. Main Orchestrator (Wire All Modules)

  **What to do**:
  - 实现 `main.py` 主入口:
    - `main()` 函数：
      1. 加载配置 (`config.load_config()`)
      2. 获取 PID 锁 (`utils.acquire_lock()`)
      3. 初始化日志 (`alerter.setup_logger()`)
      4. 获取今日调度计划 (`scheduler.get_today_schedule()`)
      5. 对每个任务：
         - 获取目标磁盘 (`utils.get_target_disk()`)
         - 如磁盘满 → `alerter.alert_disk_full()` → skip
         - 执行链审计 (`validator.audit_backup_chain()` + `validator.fuse_check()`)
         - 如链断裂 → 覆盖为全量
         - 执行备份 (`executor.backup_xxx()`)
         - 执行 MD5 校验 (`validator.verify_md5()`)
         - 如校验失败 → `alerter.alert_md5_mismatch()` → 重试1次
         - 更新状态文件 (`utils.write_status()`)
      6. 释放 PID 锁 (`utils.release_lock()`)
    - CLI 参数支持：
      - `--dry-run`: 只显示计划，不执行
      - `--once`: 执行一次后退出
      - `--job <name>`: 只执行指定任务
      - 无参数: 进入循环调度模式
  - 状态文件在每个任务完成后即时更新（非全部结束后才写入）

  **Must NOT do**:
  - 不引入 argparse 以外的 CLI 框架 (click, typer)
  - 不在 main.py 中实现业务逻辑（仅编排调用）
  - 不捕获所有异常静默处理（未知异常应 crash + 告警）

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 四模块编排 + 错误恢复流程 + CLI 参数 + 状态管理
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: CLI 模式、模块编排、错误处理

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (sequential: T12 first, then T15)
  - **Blocks**: Task 15
  - **Blocked By**: Tasks 5, 9, 10, 11

  **References**:
  - **Pattern References**: 所有前置模块的公开 API：
    - `config.load_config(path)` → dict (Task 1)
    - `scheduler.get_today_schedule(config)` → list[Job] (Task 5)
    - `executor.backup_sqlserver/mysql/sqlite/files(job, disk)` → BackupResult (Tasks 6,7,8)
    - `validator.verify_md5(path, md5_path)` → bool (Task 9)
    - `validator.audit_backup_chain(job, dir)` + `fuse_check(result)` (Task 10)
    - `alerter.send_alert(level, msg, config)` (Task 11)
    - `utils.get_target_disk()`, `acquire_lock()`, `release_lock()`, `write_status()` (Tasks 2,4)
  - **External References**: Python `argparse` 模块

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Dry-run shows schedule without executing
    Tool: Bash
    Preconditions: All modules implemented, valid config.json
    Steps:
      1. Run: python src/main.py --dry-run
      2. Assert exit code == 0
      3. Assert output contains job names from config
      4. Assert output contains "DRY RUN" indicator
      5. Assert NO backup files created
    Expected Result: Schedule displayed, nothing executed
    Failure Indicators: Files created, non-zero exit, no output
    Evidence: .omo/evidence/task-12-dry-run.txt

  Scenario: --once executes one cycle and exits
    Tool: Bash
    Preconditions: Config with at least one configured backup job (can be SQLite for testing)
    Steps:
      1. Run: python src/main.py --once
      2. Assert exit code == 0
      3. Assert backup_status.json updated with new timestamp
      4. Assert log file contains execution entries
      5. Assert process terminated (not looping)
    Expected Result: One backup cycle completed, process exits
    Failure Indicators: Process hangs, status not updated, errors in log
    Evidence: .omo/evidence/task-12-once-run.txt

  Scenario: PID lock prevents concurrent execution
    Tool: Bash
    Preconditions: main.py running
    Steps:
      1. Start: python src/main.py --once (background)
      2. Immediately run: python src/main.py --once
      3. Assert second instance exits with error message about lock
    Expected Result: Second instance refuses to run
    Evidence: .omo/evidence/task-12-pid-lock.txt
  ```

  **Commit**: YES
  - Message: `feat(main): implement orchestrator wiring all modules`
  - Files: `src/main.py`

- [x] 13. Frontend — Dashboard Charts + Data Rendering

  **What to do**:
  - 在 `dashboard/app.js` 中实现:
    - `loadData()` — fetch `backup_status.json`（相对路径），解析 JSON
    - `renderDiskChart(disks)` — 环形图显示各磁盘使用率（Chart.js Doughnut）
    - `renderTimelineChart(jobs)` — 时间线图显示最近7天备份状态（成功/失败/跳过）
    - `renderChainStatus(jobs)` — 状态卡片：每个任务的链完整性（✅ intact / ⚠️ broken）
    - `renderAlertList(alerts)` — 最近告警列表（最新在上，最多显示20条）
    - `init()` — 页面加载时调用，每60秒自动刷新
  - 图表限制：最多4种图表（环形、时间线、状态卡片、告警列表）
  - 使用 Chart.js 本地文件（已在 Task 3 HTML 中引入 `dashboard/lib/chart.min.js`，离线可用）
  - 数据为空时显示优雅的空状态提示
  - 所有时间显示为本地时区格式

  **Must NOT do**:
  - 不添加第5种图表类型
  - 不实现数据编辑/写入功能
  - 不添加路由/多页面
  - 不引入额外 JS 库（仅 Chart.js）

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: 数据可视化 + Chart.js 配置 + 响应式布局
  - **Skills**: [`frontend-design`]
    - `frontend-design`: Anthropic 官方前端设计 skill，图表配色和布局指导

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7, 8, 9, 11)
  - **Blocks**: Task 14
  - **Blocked By**: Tasks 3, 4

  **References**:
  - **Pattern References**: `dashboard/index.html` HTML 结构 (Task 3), `sample_status.json` 数据格式 (Task 4)
  - **External References**:
    - Chart.js 文档: Doughnut chart config, Line chart config
    - `fetch()` API for loading JSON
    - `Intl.DateTimeFormat` for locale-aware time display

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Dashboard renders all 4 chart types with sample data
    Tool: chrome-devtools
    Preconditions: Python HTTP server running, sample_status.json in dashboard/
    Steps:
      1. Navigate to http://localhost:8080
      2. Wait for text "备份状态" or "Backup Status" (timeout: 5s)
      3. Assert canvas elements exist (Chart.js renders to canvas): document.querySelectorAll('canvas').length >= 2
      4. Assert .chain-status or .status-card elements exist
      5. Assert .alert-list element contains at least 1 child
      6. Take screenshot
    Expected Result: All 4 visualization types rendered with data
    Failure Indicators: Blank canvas, missing elements, JS errors in console
    Evidence: .omo/evidence/task-13-charts-render.png

  Scenario: Empty data shows graceful empty state
    Tool: chrome-devtools
    Preconditions: backup_status.json with empty arrays: {"disks":[],"jobs":[],"alerts":[]}
    Steps:
      1. Navigate to http://localhost:8080
      2. Assert page does not show JS errors
      3. Assert empty state messages visible (text like "暂无数据" or "No data")
      4. Assert no broken chart renders (no NaN, no error overlays)
    Expected Result: Clean empty state, no crashes
    Evidence: .omo/evidence/task-13-empty-state.png

  Scenario: Auto-refresh updates data
    Tool: chrome-devtools
    Preconditions: Dashboard loaded with initial data
    Steps:
      1. Load page, note current content
      2. Modify backup_status.json (add a new alert entry)
      3. Wait 65 seconds (refresh interval is 60s)
      4. Assert new alert appears in the alert list without manual reload
    Expected Result: Dashboard auto-updates with new data
    Evidence: .omo/evidence/task-13-auto-refresh.png
  ```

  **Commit**: NO (groups with Task 14)

- [x] 14. Frontend — Apple Polish (Glassmorphism, Animations)

  **What to do**:
  - 在 `dashboard/style.css` 中完善 Apple 风格:
    - 卡片悬浮效果: `transition: transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94); &:hover { transform: scale(1.02); box-shadow: 0 20px 60px rgba(0,0,0,0.3) }`
    - 页面加载动画: 卡片依次淡入 `@keyframes fadeInUp` + `animation-delay` 递增
    - 图表颜色配方: 使用 Apple 风格渐变色（蓝→紫、绿→青）
    - 状态指示器: 成功=绿色脉冲点, 失败=红色脉冲点, `@keyframes pulse`
    - 深色/浅色模式切换: `prefers-color-scheme` 媒体查询 + 手动切换按钮
    - 响应式: 大屏4列 Grid → 中屏2列 → 小屏1列
    - 微交互: 按钮点击 `scale(0.97)` 反馈
  - 在 `dashboard/app.js` 中添加:
    - 主题切换函数 `toggleTheme()`
    - Chart.js 主题适配（深色/浅色配色方案）
    - 数字变化时的计数动画（从旧值滑动到新值）

  **Must NOT do**:
  - 不引入动画库（纯 CSS 动画 + requestAnimationFrame）
  - 不过度装饰（保持 Apple 克制风格：少即是多）
  - 不添加音效或视频

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: 纯视觉优化 + 动效设计 + 响应式适配
  - **Skills**: [`frontend-design`]
    - `frontend-design`: Apple 风格微交互、动效曲线、配色指导

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 13 chart structure)
  - **Parallel Group**: Wave 3 (with Task 10)
  - **Blocks**: Task 15
  - **Blocked By**: Task 13

  **References**:
  - **Pattern References**: `dashboard/style.css` 基础样式 (Task 3), `dashboard/app.js` 图表代码 (Task 13)
  - **External References**:
    - CSS `cubic-bezier()` 缓动函数: Apple 使用 `cubic-bezier(0.25, 0.46, 0.45, 0.94)`
    - `@media (prefers-color-scheme: dark)` MDN docs
    - CSS Grid `repeat(auto-fit, minmax(300px, 1fr))` 响应式模式

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Hover animation on cards
    Tool: chrome-devtools
    Preconditions: Dashboard loaded
    Steps:
      1. Navigate to http://localhost:8080
      2. Hover over first .card element
      3. Wait 300ms for transition
      4. Assert computed transform contains "scale" or "matrix" indicating scale > 1
      5. Assert box-shadow is present and non-zero
      6. Take screenshot during hover state
    Expected Result: Card scales up with shadow on hover
    Evidence: .omo/evidence/task-14-hover-animation.png

  Scenario: Dark/Light mode toggle works
    Tool: chrome-devtools
    Preconditions: Dashboard loaded in dark mode (default)
    Steps:
      1. Assert body background-color is dark (#1c1c1e or rgb close to it)
      2. Click theme toggle button (selector: .theme-toggle or #theme-btn)
      3. Wait 300ms for transition
      4. Assert body background-color is light (#f5f5f7 or similar)
      5. Assert Chart.js canvas redraws with light theme colors
      6. Take screenshots of both states
    Expected Result: Theme switches smoothly between dark and light
    Evidence: .omo/evidence/task-14-theme-toggle.png

  Scenario: Responsive layout adapts to viewport
    Tool: chrome-devtools
    Preconditions: Dashboard loaded
    Steps:
      1. Set viewport to 1440px width - assert 4-column grid
      2. Set viewport to 768px width - assert 2-column grid
      3. Set viewport to 375px width - assert 1-column grid (mobile)
      4. Take screenshot at each breakpoint
    Expected Result: Grid collapses gracefully at each breakpoint
    Evidence: .omo/evidence/task-14-responsive-1440.png, task-14-responsive-768.png, task-14-responsive-375.png
  ```

  **Commit**: YES
  - Message: `feat(dashboard): add Apple-style visualization dashboard`
  - Files: `dashboard/index.html`, `dashboard/app.js`, `dashboard/style.css`

- [x] 15. Integration Test + End-to-End Wiring

  **What to do**:
  - 创建 `run_server.py` — 一键启动脚本:
    - 启动 Python HTTP server（serving dashboard/ 目录，port 8080）
    - 后台运行备份主循环（可选）
    - 支持 `python src/run_server.py --dashboard-only` 仅启动前端服务
  - 端到端验证:
    - 确保 `main.py --once` 执行后 `backup_status.json` 被正确写入
    - 确保前端能读取并渲染该文件
    - 确保告警日志文件在 `logs/` 目录下正确创建
  - 创建 `README_USAGE.md` (简短启动说明，非文档，仅操作步骤):
    - 如何配置 `config.json`
    - 如何启动备份: `python src/main.py --once`
    - 如何查看 Dashboard: `python src/run_server.py --dashboard-only`
  - 文件结构验证：确保最终目录结构清晰

  **Must NOT do**:
  - 不创建 Docker 配置
  - 不添加复杂的部署流程
  - README 不超过50行

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 集成验证 + 脚本编写 + 端到端确认
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: 项目入口脚本模式

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (sequential after Task 12)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 12, 14

  **References**:
  - **Pattern References**: 所有前置任务的产出文件
  - **External References**: Python `http.server` 模块, `threading` for background server

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: End-to-end: backup runs → status updates → dashboard shows result
    Tool: chrome-devtools + Bash
    Preconditions: Full system assembled, config.json with at least SQLite test job
    Steps:
      1. Bash: python src/main.py --once --job sqlite_test
      2. Assert exit code == 0
      3. Assert backup_status.json updated (last_updated changed)
      4. Bash: Start python src/run_server.py --dashboard-only (background)
      5. chrome-devtools: Navigate to http://localhost:8080
      6. Assert dashboard shows the SQLite job with "success" status
      7. Assert disk usage chart renders
      8. Take screenshot
    Expected Result: Full pipeline works: backup → validate → status → display
    Failure Indicators: Status not updated, dashboard shows stale/empty data
    Evidence: .omo/evidence/task-15-e2e-flow.png

  Scenario: Dashboard-only mode starts without backup engine
    Tool: Bash + chrome-devtools
    Preconditions: run_server.py exists, sample_status.json available
    Steps:
      1. Bash: python src/run_server.py --dashboard-only &
      2. Wait 2 seconds for server start
      3. chrome-devtools: Navigate to http://localhost:8080
      4. Assert page loads successfully
      5. Assert no backup processes started (check PID file not created)
    Expected Result: Only web server runs, no backup execution
    Evidence: .omo/evidence/task-15-dashboard-only.txt
  ```

  **Commit**: YES
  - Message: `feat(integration): add run_server.py and usage documentation`
  - Files: `run_server.py`, `README_USAGE.md`

- [x] 16. Final Packaging (Offline Deployment Bundle)

  **What to do**:
  - 使用 `uv pip compile` 生成锁定的 `requirements.txt`
  - 使用 `uv pip download` 预下载所有依赖 wheel 到 `vendor/` 目录（含 paramiko + 依赖链）
  - 创建 `install.bat` — 离线安装脚本：
    ```bat
    @echo off
    echo [1/3] Creating virtual environment...
    uv venv .venv
    echo [2/3] Installing dependencies (offline)...
    uv pip install --no-index --find-links vendor -r requirements.txt
    echo [3/3] Done! Run start.bat to begin.
    ```
  - 创建 `start.bat` — 一键启动脚本：
    ```bat
    @echo off
    .venv\Scripts\activate && python src/main.py %*
    ```
  - 创建 `start_dashboard.bat`:
    ```bat
    @echo off
    .venv\Scripts\activate && python src/run_server.py --dashboard-only
    ```
  - 打包为 `backup-system-release.zip`，包含：
    - `src/` — 所有 Python 源码
    - `dashboard/` — 前端文件
    - `vendor/` — 离线依赖 wheel 包
    - `config.example.json` — 配置模板
    - `requirements.txt` — 锁定依赖列表
    - `pyproject.toml` — 项目元数据
    - `install.bat` — 离线安装脚本
    - `start.bat` — 备份启动脚本
    - `start_dashboard.bat` — Dashboard 启动脚本
    - `README_USAGE.md` — 使用说明
  - 排除：`.venv/`, `logs/`, `.omo/`, `*.bak`, `backup_status.json`
  - 验证：在干净目录解压后，`install.bat` + `start.bat --dry-run` 可正常运行

  **Must NOT do**:
  - 不打包 .venv 目录本身（只打包 wheel，由目标机器创建 venv）
  - 不依赖网络下载（vendor/ 包含所有离线 wheel）
  - 不打包日志文件或运行时产物
  - 不使用 exe 打包工具（PyInstaller 等）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 依赖解析 + 离线打包 + bat 脚本编写 + zip 构建
  - **Skills**: [`python-best-practices`]
    - `python-best-practices`: uv 工作流、依赖管理、项目打包

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (after Task 15, last implementation task)
  - **Blocks**: F1-F4
  - **Blocked By**: Task 15

  **References**:
  - **Pattern References**: `pyproject.toml` (Task 1), 所有 src/ 源码 (Tasks 1-12)
  - **External References**:
    - `uv pip compile pyproject.toml -o requirements.txt`
    - `uv pip download -r requirements.txt -d vendor/`
    - Python `zipfile` 模块 or PowerShell `Compress-Archive`

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Offline install works in clean directory
    Tool: Bash
    Preconditions: backup-system-release.zip created, extract to C:\Users\dingxiao\AppData\Local\Temp\opencode\test-deploy
    Steps:
      1. Extract zip to temp directory
      2. Run: install.bat
      3. Assert .venv directory created
      4. Assert .venv\Scripts\python.exe exists
      5. Run: start.bat --dry-run
      6. Assert exit code == 0
      7. Assert output shows scheduled jobs
    Expected Result: Full system works from zip without internet
    Failure Indicators: pip download errors, import errors, missing modules
    Evidence: .omo/evidence/task-16-offline-install.txt

  Scenario: Zip contains all required files, no runtime artifacts
    Tool: Bash
    Preconditions: backup-system-release.zip exists
    Steps:
      1. List zip contents: python -c "import zipfile; z=zipfile.ZipFile('backup-system-release.zip'); [print(f) for f in sorted(z.namelist())]"
      2. Assert contains: src/, dashboard/, vendor/, config.example.json, install.bat, start.bat, requirements.txt
      3. Assert NOT contains: .venv/, logs/, *.bak, backup_status.json, .omo/
    Expected Result: Clean release package with no runtime artifacts
    Evidence: .omo/evidence/task-16-zip-contents.txt

  Scenario: vendor/ contains all dependency wheels
    Tool: Bash
    Preconditions: vendor/ directory populated
    Steps:
      1. Assert vendor/ contains paramiko*.whl
      2. Assert vendor/ contains cryptography*.whl (paramiko dep)
      3. Assert vendor/ contains all transitive dependencies
      4. Run: uv pip install --no-index --find-links vendor -r requirements.txt --dry-run
      5. Assert all packages would be satisfied from vendor/
    Expected Result: All dependencies available offline
    Evidence: .omo/evidence/task-16-vendor-check.txt
  ```

  **Commit**: YES
  - Message: `chore(release): add offline packaging with vendor dependencies`
  - Files: `install.bat`, `start.bat`, `start_dashboard.bat`, `vendor/`, `requirements.txt`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .omo/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `python -m compileall src/` for syntax check. Review all Python files in `src/` for: bare except, print in prod code, hardcoded credentials, unused imports, global state. Check AI slop: excessive comments, over-abstraction, generic variable names. Verify module boundaries (scheduler doesn't import executor internals).
  Output: `Syntax [PASS/FAIL] | Files [N clean/N issues] | Module Boundaries [PASS/FAIL] | VERDICT`

- [x] F3. **Real Manual QA via chrome-devtools** — `visual-engineering`
  Start Python HTTP server. Open dashboard in chrome-devtools. Verify: Apple-style visual (glassmorphism cards, rounded corners ≥16px, backdrop-filter blur, dark mode). Test all chart renders with sample data. Check responsive behavior. Take screenshots as evidence. Save to `.omo/evidence/final-qa/`.
  Output: `Visual [PASS/FAIL] | Charts [N/N render] | Responsive [PASS/FAIL] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual files. Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-module contamination. Flag unaccounted files.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1**: `feat(core): add project scaffolding, config, disk scanner, data models` — src/config.py, src/utils.py, pyproject.toml, config.example.json, sample_status.json, dashboard/index.html, dashboard/style.css
- **Wave 2-a**: `feat(scheduler): implement backup schedule decision logic` — src/scheduler.py
- **Wave 2-b**: `feat(executor): implement all backup executors` — src/executor.py
- **Wave 2-c**: `feat(validator): implement MD5 checksum verification` — src/validator.py
- **Wave 2-d**: `feat(alerter): implement logging and email notification` — src/alerter.py
- **Wave 2-e**: `feat(dashboard): add chart rendering and data loading` — dashboard/app.js
- **Wave 3**: `feat(validator): add chain audit and fuse mechanism` — src/validator.py
- **Wave 3-b**: `feat(dashboard): add Apple-style polish and animations` — dashboard/style.css, dashboard/app.js
- **Wave 4-a**: `feat(main): implement orchestrator wiring all modules` — src/main.py
- **Wave 4-b**: `feat(integration): add run_server and usage docs` — src/run_server.py, README_USAGE.md
- **Wave 4-c**: `chore(release): add offline packaging with vendor dependencies` — install.bat, start.bat, start_dashboard.bat, vendor/, requirements.txt

---

## Success Criteria

### Verification Commands
```bash
python src/main.py --dry-run          # Expected: shows scheduled jobs without executing
python src/main.py --once             # Expected: runs one backup cycle, exits 0
python src/run_server.py --dashboard-only  # Expected: serves dashboard at localhost:8080
```

### Runtime Requirements
- **Python**: 3.11+ (required for modern type annotations and match statements)
- **OS**: Windows Server (backup server), Linux (MySQL source servers)
- **Network**: 企业内网，无互联网访问，所有依赖离线安装
- **Dashboard access**: localhost:8080 only (no auth, no external binding)
- **Backup retention**: 30 天后由操作系统计划任务或手动清理（V1 不实现自动删除）

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] `python src/main.py --dry-run` exits 0 with correct schedule display
- [ ] Dashboard renders in chrome with Apple-style visuals
- [ ] MD5 validation logs show PASS for intact files
- [ ] Chain audit correctly identifies gaps
- [ ] Fuse mechanism triggers full backup on chain break
- [ ] Multi-disk rotation switches when space insufficient
- [ ] `install.bat` completes without internet
- [ ] `backup-system-release.zip` contains all files, no runtime artifacts
