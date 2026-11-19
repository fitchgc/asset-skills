# Asynx Asset Manager 配置与维护

命令中的 `<skill-dir>` 为当前 Skill 的绝对路径。`scan`、`list` 与 `lineage` 均为本地离线命令，不需要授权或联网即可运行。

## Plaiers Agent 授权与 JWT

需要执行 `whoami`、`doctor --verify` 或从统一工作台发起云端 Generation Task 时，运行 `.plaiers-context.json` 记录的社区凭据桥接取得短期 JWT（需 `skill_jwt` 权限）。该文件由 Plaiers 安装器写入；Skill 不读取 PAT，也不缓存 JWT：

```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```

在无交互自动化环境可设置 `ASYNX_PARTNER_TOKEN` / `ASYNX_BASE_URL` 环境变量。

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
