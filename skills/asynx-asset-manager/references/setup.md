# Asynx Asset Manager 配置与维护

命令中的 `<skill-dir>` 为当前 Skill 的绝对路径。`scan`、`list` 与 `lineage` 均为本地离线命令，不需要登录账号或联网即可运行。

## Identity Service 会话管理

需要执行 `whoami`、`doctor --verify` 或从统一工作台发起云端 Generation Task 时，使用统一 Identity Service 会话（与 `asynx-image-pro`、`asynx-audio`、`asynx-video`、`asynx-3d` 共享 `~/.config/asynx/config.json`）：

```bash
python3 "<skill-dir>/scripts/asynx.py" login
python3 "<skill-dir>/scripts/asynx.py" whoami
python3 "<skill-dir>/scripts/asynx.py" logout
```

在无交互自动化环境可传入 `--no-login-ui` 或设置 `ASYNX_PARTNER_TOKEN` / `ASYNX_BASE_URL` 环境变量。

## 配置检查与环境诊断

```bash
python3 "<skill-dir>/scripts/asynx.py" config status
python3 "<skill-dir>/scripts/asynx.py" doctor
python3 "<skill-dir>/scripts/asynx.py" doctor --verify
```

## 安装与更新

在仓库根目录通过 `install.py` 安装或单独安装本 Skill：

```bash
python3 install.py --skill asset-manager
python3 install.py --skill all
```

已安装副本可通过 `update` 子命令检查与更新：

```bash
python3 "<skill-dir>/scripts/asynx.py" update status
python3 "<skill-dir>/scripts/asynx.py" update check
python3 "<skill-dir>/scripts/asynx.py" update apply
python3 "<skill-dir>/scripts/asynx.py" update rollback
```
