# 数据备份系统 — 使用说明
## 快速开始
1. 安装依赖（首次）：`install.bat`
2. 配置：复制 `config.example.json` 为 `config.json`，填写 `databases`、`disks.min_free_gb`（默认 100 GB）、`email`
3. 手动备份一次：`start.bat --once`
4. 持续调度：`start.bat`
5. 查看 Dashboard：`start_dashboard.bat`，访问 http://127.0.0.1:8080

## 常用命令
| 命令 | 说明 |
|------|------|
| `start.bat --dry-run` | 查看今日调度计划 |
| `start.bat --once` | 执行一次全部任务后退出 |
| `start.bat --once --job mysql_app` | 只执行指定任务 |
| `start_dashboard.bat` | 启动 Dashboard |

## 目录结构
```
backup-system-release/
├── src/        # Python 源码
├── dashboard/  # 前端可视化
├── vendor/     # 离线依赖
├── config.json # 配置文件
├── install.bat # 首次安装
├── start.bat   # 启动备份
└── start_dashboard.bat
```
