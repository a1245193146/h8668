# 数据备份系统 V2 增强 — Work Plan

## TL;DR

> **Quick Summary**: 扩展现有备份系统：支持多MySQL库、远程文件备份(Linux密码+Windows WinRM/SSH)、备份到远程Windows机器(多盘轮询)、月度自动恢复验证。
> 
> **Deliverables**:
> - 新增 `src/connector.py` — 统一远程连接层（SSH密码、WinRM、SSH回退）
> - 重写 `src/executor.py` — 远程文件备份 + MySQL密码认证
> - 新增 `src/restore_verifier.py` — 月度自动恢复验证模块
> - 重写 `src/utils.py` — 远程磁盘扫描 + 远程文件传输
> - 更新 `config.example.json` — 多MySQL库 + 远程目标 + 测试机配置
> - 更新 `vendor/` — 添加 pywinrm + 依赖 wheels
> - 重新打包 `backup-system-release.zip`
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: T1 → T3 → T5 → T7 → T8 → T9 → F1-F4

---

## Context

### Original System (已完成)
- 4模块架构：scheduler / executor / validator / alerter
- 本地备份：SQL Server(sqlcmd) + MySQL(SSH密钥) + SQLite + 文件(本地)
- 本地存储：多盘>2TB轮询
- 前端：Apple风格Dashboard

### V2 需求变更

| # | 原方案 | 新需求 |
|---|--------|--------|
| 1 | 单MySQL库 | 多MySQL库，每个独立备份 |
| 2 | SSH密钥认证 | Linux统一账号密码登录 |
| 3 | 无Windows远程 | WinRM优先 + SSH回退（域管理员） |
| 4 | 本地文件备份 | 远程服务器文件(Linux+Windows) |
| 5 | 本地多盘存储 | 备份到远程Windows机器(多盘轮询) |
| 6 | V1仅日志提醒 | 月度自动恢复验证(MySQL+SQL Server) |

### 技术决策
- **Linux连接**: paramiko SSH + 密码认证（非密钥）
- **Windows连接**: pywinrm 优先，SSH 回退
- **文件备份策略**: 周日全量 + 工作日增量（基于mtime）
- **备份目标**: 远程Windows机器，WinRM/SSH推送，远程多盘轮询
- **恢复验证**: 每月1号自动执行，2台测试机(Linux MySQL + Windows SQL Server)
- **pywinrm**: 加入 vendor 离线包

---

## Work Objectives

### Core Objective
扩展数据备份系统，实现全远程架构：从远程服务器拉取文件/数据库备份，推送到远程存储Windows机器，并每月自动验证恢复可用性。

### Concrete Deliverables
- `src/connector.py` — 统一远程连接抽象层（SSH密码/WinRM/SSH回退）
- `src/executor.py` — 重写：远程文件备份 + MySQL密码认证
- `src/restore_verifier.py` — 新模块：自动恢复验证
- `src/utils.py` — 扩展：远程磁盘扫描 + 远程文件推送
- `config.example.json` — 扩展：多MySQL + 远程源/目标/测试机配置
- `vendor/` — 新增 pywinrm + requests + 依赖 wheels
- `backup-system-release.zip` — 重新打包

### Must Have
- 统一连接器：一个接口适配 SSH密码/WinRM/SSH回退 三种方式
- 多MySQL库支持：配置N个MySQL数据库，每个独立调度
- 远程文件备份：Linux(SSH密码拉取) + Windows(WinRM/SSH拉取)
- 远程存储：备份文件推送到远程Windows机器，远程多盘>2TB轮询
- 月度恢复验证：自动还原MySQL到Linux测试机 + SQL Server到Windows测试机
- 域管理员凭据：Windows连接使用域账号 `DOMAIN\admin`
- 向后兼容：不破坏现有 scheduler/validator/alerter/dashboard 功能

### Must NOT Have
- ❌ Agent 安装到远程机器（纯远程命令执行）
- ❌ 实时文件同步（仅定时备份）
- ❌ 自动修复恢复失败（只报告结果 + 告警）
- ❌ 新的前端页面（Dashboard 自动显示新任务，无需改动）

### Defaults Applied
- **WinRM端口**: 5985 (HTTP) / 5986 (HTTPS)，默认 5985
- **SSH端口**: 22（Linux和Windows统一）
- **WinRM超时**: 300秒（大文件传输）
- **恢复验证日**: 每月1日 03:00 UTC
- **恢复验证查询**: `SELECT COUNT(*) FROM <table>` 验证数据存在
- **远程磁盘扫描间隔**: 每次备份前实时扫描

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision
- **Backend**: Agent QA — 运行连接器测试、模拟远程操作
- **Frontend**: chrome-devtools — 验证新任务在Dashboard中正确显示
- **Integration**: 端到端备份流程（可用SQLite本地测试替代远程）

### QA Policy
- PYTHONPATH=src 规则不变
- Evidence saved to `.omo/evidence/v2-task-{N}-*.txt`
- WinRM/SSH测试：无真实远程机器时，验证连接失败的优雅处理

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── Task 1: Config expansion (multi-MySQL, remote sources, targets, test machines) [quick]
├── Task 2: Add pywinrm to dependencies + vendor wheels [quick]
├── Task 3: src/connector.py — unified remote connection layer [deep]

Wave 2 (Core Rewrites — 3 parallel, after Wave 1):
├── Task 4: Rewrite executor.py — MySQL password auth + multiple DBs [unspecified-high]
├── Task 5: Rewrite executor.py — remote file backup (Linux SSH + Windows WinRM) [deep]
├── Task 6: Rewrite utils.py — remote disk scan + remote file push [unspecified-high]

Wave 3 (New Module — after Wave 2):
├── Task 7: src/restore_verifier.py — auto-restore to test machines [deep]
├── Task 8: Update main.py — wire restore_verifier + monthly trigger [unspecified-high]

Wave 4 (Packaging — after Wave 3):
├── Task 9: Re-package backup-system-release.zip + update README [quick]

Wave FINAL (4 parallel reviews):
├── F1. Plan compliance audit
├── F2. Code quality review
├── F3. Integration QA
├── F4. Scope fidelity check
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | — | 4,5,6,7,8 | 1 |
| 2 | — | 4,5,6,9 | 1 |
| 3 | — | 4,5,6,7 | 1 |
| 4 | 1,2,3 | 8 | 2 |
| 5 | 1,2,3 | 8 | 2 |
| 6 | 1,2,3 | 7,8 | 2 |
| 7 | 3,6 | 8 | 3 |
| 8 | 4,5,6,7 | 9 | 3 |
| 9 | 8 | F1-F4 | 4 |

---

## TODOs

- [x] 1. Config Expansion (multi-MySQL, remote sources, targets, test machines)

  **What to do**:
  - 扩展 `config.example.json`:
    - `databases[]` 支持多个 MySQL 条目（不同 host/db/user）
    - 每个 MySQL 条目添加: `ssh_password` 字段（替代 ssh_key）
    - 新增 `remote_file_sources[]`: 远程文件备份源列表
      ```json
      {
        "name": "linux_logs",
        "type": "file",
        "os": "linux",
        "host": "192.168.1.20",
        "port": 22,
        "username": "backup_user",
        "password": "<password>",
        "source_dirs": ["/var/log", "/opt/app/data"],
        "schedule_time": "03:00"
      }
      ```
    - Windows文件源:
      ```json
      {
        "name": "win_docs",
        "type": "file",
        "os": "windows",
        "host": "192.168.1.30",
        "winrm_port": 5985,
        "ssh_port": 22,
        "domain": "CORP",
        "username": "admin",
        "password": "<domain-password>",
        "source_dirs": ["C:\\SharedDocs", "D:\\AppData"],
        "schedule_time": "03:30"
      }
      ```
    - 新增 `backup_target`: 远程存储目标
      ```json
      "backup_target": {
        "host": "192.168.1.100",
        "os": "windows",
        "winrm_port": 5985,
        "ssh_port": 22,
        "domain": "CORP",
        "username": "admin",
        "password": "<domain-password>",
        "base_path": "E:\\Backups",
        "min_size_tb": 2,
        "min_free_gb": 100
      }
      ```
    - 新增 `restore_test`: 恢复验证配置
      ```json
      "restore_test": {
        "enabled": true,
        "schedule_day": 1,
        "schedule_time": "03:00",
        "linux_machine": {
          "host": "192.168.1.200",
          "port": 22,
          "username": "root",
          "password": "<password>",
          "mysql_user": "root",
          "mysql_password": "<mysql-root-password>",
          "test_db": "restore_test_db"
        },
        "windows_machine": {
          "host": "192.168.1.201",
          "winrm_port": 5985,
          "ssh_port": 22,
          "domain": "CORP",
          "username": "admin",
          "password": "<domain-password>",
          "sqlserver_instance": "localhost",
          "test_db": "restore_test_db"
        }
      }
      ```
  - 更新 `src/config.py` 的 `_REQUIRED_TOP_LEVEL_KEYS` 集合
  - 验证多MySQL条目的唯一性（name不重复）

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-best-practices`]

  **QA Scenarios**:
  ```
  Scenario: Config loads with multiple MySQL databases
    Steps: Load config with 3 MySQL entries, assert all 3 parsed correctly
    Evidence: .omo/evidence/v2-task-1-multi-mysql.txt
  ```

  **Commit**: YES — `feat(config): expand for multi-MySQL, remote sources, targets, restore test`

- [x] 2. Add pywinrm to Dependencies + Vendor Wheels

  **What to do**:
  - `uv add pywinrm` — 添加到 pyproject.toml
  - 重新生成 `requirements.txt`
  - 下载新 wheels 到 `vendor/`: pywinrm + requests + urllib3 + charset-normalizer + idna + certifi
  - 验证 `uv pip install --no-index --find-links vendor -r requirements.txt` 能离线安装

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-best-practices`]

  **QA Scenarios**:
  ```
  Scenario: pywinrm importable after install
    Steps: python -c "import winrm; print(winrm.__version__)"
    Evidence: .omo/evidence/v2-task-2-winrm-import.txt
  ```

  **Commit**: YES — `feat(deps): add pywinrm for Windows remote management`

- [x] 3. src/connector.py — Unified Remote Connection Layer

  **What to do**:
  - 创建 `src/connector.py` 提供统一接口:
    - `class RemoteConnector(Protocol)`:
      - `exec_command(cmd: str) -> tuple[int, str, str]` (exit_code, stdout, stderr)
      - `upload_file(local_path, remote_path) -> bool`
      - `download_file(remote_path, local_path) -> bool`
      - `list_dir(remote_path) -> list[str]`
      - `disk_usage(path) -> dict` (total_gb, free_gb, used_gb)
      - `close()`
    - `class SSHPasswordConnector(RemoteConnector)`:
      - paramiko SSH with username+password (非密钥)
      - SFTP for file upload/download
      - 重试逻辑: 3次, 间隔30秒
    - `class WinRMConnector(RemoteConnector)`:
      - pywinrm Session with NTLM auth (`DOMAIN\user`)
      - PowerShell命令执行
      - 文件传输: 通过 PowerShell Base64 编码传输（小文件）
        或通过临时 SMB 共享（大文件，回退到 SSH）
    - `class WindowsConnector(RemoteConnector)`:
      - 组合器: 先尝试 WinRM, 失败则回退 SSH
      - `connect()` 自动选择可用协议
    - `def create_connector(config: dict) -> RemoteConnector`:
      - 根据 `os` 字段选择连接器类型:
        - `linux` → SSHPasswordConnector
        - `windows` → WindowsConnector (WinRM + SSH fallback)

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-best-practices`, `ssh-remote`]

  **QA Scenarios**:
  ```
  Scenario: SSHPasswordConnector handles connection failure gracefully
    Steps: Connect to unreachable host, assert returns error without crash
    Evidence: .omo/evidence/v2-task-3-ssh-fail.txt

  Scenario: WinRMConnector handles connection failure gracefully
    Steps: Connect to unreachable host, assert returns error without crash
    Evidence: .omo/evidence/v2-task-3-winrm-fail.txt

  Scenario: WindowsConnector tries WinRM then falls back to SSH
    Steps: Mock WinRM failure, verify SSH attempt is made
    Evidence: .omo/evidence/v2-task-3-fallback.txt
  ```

  **Commit**: YES — `feat(connector): unified remote connection layer (SSH password + WinRM + fallback)`

- [x] 4. Rewrite executor.py — MySQL Password Auth + Multiple DBs

  **What to do**:
  - 修改 `backup_mysql()`:
    - 使用 `SSHPasswordConnector` 替代直接 paramiko 密钥连接
    - 支持 `ssh_password` 字段（之前是 `ssh_key`）
    - 向后兼容：如果 `ssh_key` 存在则用密钥，否则用密码
  - 确保 `get_today_schedule()` 返回多个MySQL任务（已支持，因为config.databases是数组）
  - 验证多个MySQL库可以按各自 schedule_time 独立备份

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-best-practices`, `mysql`, `ssh-remote`]

  **Commit**: YES — `feat(executor): MySQL password auth via connector + multi-DB support`

- [x] 5. Rewrite executor.py — Remote File Backup (Linux SSH + Windows WinRM)

  **What to do**:
  - 重写 `backup_files()`:
    - 根据 `job_config['os']` 选择连接器
    - Linux: `SSHPasswordConnector` → 远程 `tar -czf` 打包 → SFTP下载
    - Windows: `WindowsConnector` → 远程 `Compress-Archive` 或 `tar` → 下载
    - 增量逻辑: 远程执行 `find -newer` (Linux) 或 PowerShell `-Filter` (Windows)
    - 文件命名: `{job_name}_{YYYYMMDD}_{full|incr}.tar.gz` (Linux) / `.zip` (Windows)
    - 全量: 周日，打包整个目录
    - 增量: 工作日，只打包 mtime > 上次备份的文件
  - 新增 `backup_remote_files_linux(job_config, connector, target_path)`:
    - 远程执行: `tar -czf /tmp/{name}.tar.gz -C {source_dir} .`
    - 下载到本地临时目录
    - 计算MD5 + 写 sidecar
  - 新增 `backup_remote_files_windows(job_config, connector, target_path)`:
    - 远程执行: `Compress-Archive -Path "{source_dir}\*" -DestinationPath "C:\Temp\{name}.zip"`
    - 下载到本地临时目录
    - 计算MD5 + 写 sidecar

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-best-practices`, `ssh-remote`, `linux-server-expert`]

  **Commit**: YES — `feat(executor): remote file backup via SSH/WinRM (Linux + Windows)`

- [x] 6. Rewrite utils.py — Remote Disk Scan + Remote File Push

  **What to do**:
  - 新增 `scan_remote_disks(connector, min_size_tb=2.0) -> list[dict]`:
    - Windows远程: `Get-WmiObject Win32_LogicalDisk | Select Size,FreeSpace,DeviceID`
    - 解析输出，返回与本地 `scan_large_disks()` 相同格式
  - 新增 `get_remote_target_disk(connector, min_size_tb, min_free_gb) -> str | None`:
    - 调用 `scan_remote_disks()`，返回第一个有空间的磁盘
  - 新增 `push_file_to_remote(local_path, remote_path, connector) -> bool`:
    - 将本地备份文件上传到远程存储机器
    - MD5验证: 上传后远程计算MD5，与本地对比
  - 保留原有 `scan_large_disks()` / `get_target_disk()` 作为备用

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-best-practices`, `ssh-remote`]

  **Commit**: YES — `feat(utils): remote disk scanning and file push via connector`

- [x] 7. src/restore_verifier.py — Auto-Restore to Test Machines

  **What to do**:
  - 新模块 `src/restore_verifier.py`:
    - `verify_mysql_restore(config, connector) -> RestoreResult`:
      - 连接Linux测试机
      - 找到最近的MySQL全备文件
      - 远程执行: `mysql -u root -p{pass} -e "DROP DATABASE IF EXISTS {test_db}; CREATE DATABASE {test_db}"`
      - 远程执行: `gunzip < {backup_file} | mysql -u root -p{pass} {test_db}`
      - 验证: `mysql -u root -p{pass} {test_db} -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{test_db}'"`
      - 返回 RestoreResult: `{success, tables_count, duration_seconds, error_msg}`
    - `verify_sqlserver_restore(config, connector) -> RestoreResult`:
      - 连接Windows测试机 (WinRM/SSH)
      - 找到最近的SQL Server全备文件
      - 远程执行: `RESTORE DATABASE [{test_db}] FROM DISK=N'{backup_path}' WITH REPLACE`
      - 验证: `SELECT COUNT(*) FROM [{test_db}].sys.tables`
      - 返回 RestoreResult
    - `run_monthly_verification(config) -> list[RestoreResult]`:
      - 检查今天是否为每月1日
      - 分别执行 MySQL 和 SQL Server 恢复验证
      - 成功/失败都通过 alerter 报告

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-best-practices`, `mysql`, `mssql`, `database-backups`]

  **Commit**: YES — `feat(restore): monthly auto-restore verification module`

- [x] 8. Update main.py — Wire restore_verifier + Monthly Trigger

  **What to do**:
  - 在 `main.py` 中:
    - 导入 `restore_verifier`
    - 在 `run_once()` 和 `schedule_loop` 中: 
      - 每次循环检查 `is_monthly_verification_day(config)`
      - 如果是 → 执行 `restore_verifier.run_monthly_verification(config)`
    - 更新 `_run_job()`: 备份完成后推送到远程存储（调用 `utils.push_file_to_remote()`）
    - 更新目标磁盘获取: 使用远程扫描替代本地扫描
  - 添加 CLI 参数: `--verify-restore` 强制立即执行恢复验证

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-best-practices`]

  **Commit**: YES — `feat(main): wire restore verifier + remote storage push`

- [x] 9. Re-package backup-system-release.zip + Update README

  **What to do**:
  - 重新下载 vendor wheels (含 pywinrm + requests 依赖链)
  - 更新 `requirements.txt`
  - 更新 `README_USAGE.md` 添加:
    - 新增配置项说明（多MySQL、远程文件源、远程目标、恢复验证）
    - 新增 `--verify-restore` 命令说明
  - 重新打包 `backup-system-release.zip`
  - 验证解压后 `install.bat` + `start.bat --dry-run` 正常

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-best-practices`]

  **Commit**: YES — `chore(release): re-package with pywinrm and updated config`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — verify all Must Have items implemented
- [x] F2. **Code Quality Review** — syntax + module boundaries + no bare except
- [x] F3. **Integration QA** — dry-run with new config, connector failure paths
- [x] F4. **Scope Fidelity Check** — no creep, all tasks implemented as specified

---

## Commit Strategy

- **Wave 1**: `feat(config): expand for v2` + `feat(deps): add pywinrm` + `feat(connector): unified remote layer`
- **Wave 2**: `feat(executor): remote MySQL + file backup` + `feat(utils): remote disk + push`
- **Wave 3**: `feat(restore): monthly verification` + `feat(main): wire v2 modules`
- **Wave 4**: `chore(release): re-package v2`

---

## Success Criteria

### Verification Commands
```bash
python src/main.py --config config.example.json --dry-run       # Shows multi-MySQL + remote file jobs
python src/main.py --config config.example.json --verify-restore # Triggers restore verification
```

### Final Checklist
- [ ] 多MySQL库出现在 --dry-run 输出中
- [ ] 远程文件源(Linux+Windows)出现在 --dry-run 输出中
- [ ] connector.py 优雅处理连接失败(WinRM回退SSH)
- [ ] restore_verifier.py 可独立调用
- [ ] vendor/ 含 pywinrm wheels
- [ ] backup-system-release.zip 更新
