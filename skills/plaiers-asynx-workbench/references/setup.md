# Asynx Asset Manager 配置与维护

命令中的 `<skill-dir>` 为当前 Skill 的绝对路径。`scan`、`list` 与 `lineage` 均为本地离线命令，不需要授权或联网即可运行。

## Plaiers Agent 授权与 JWT

需要执行 `whoami`、`doctor --verify` 或从统一工作台发起云端 Generation Task 时，运行 `.plaiers-context.json` 记录的社区凭据桥接取得短期 JWT（需 `skill_jwt` 权限）。该文件由 Plaiers 安装器写入；Skill 不读取 PAT，也不缓存 JWT：

```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```

无交互自动化环境同样经桥接取得 JWT（安装时显式绑定站点、Agent、账号和凭据文件）。`ASYNX_PARTNER_TOKEN` / `ASYNX_BASE_URL` 已移除，设置后返回 `legacy_credentials_removed`。

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

```bash
python3 "<skill-dir>/scripts/asynx.py" update status
```

`update status`（`update check` 等同）向安装时记录的 Plaiers 站点技能库查询：返回 `installed_version`（已装的锁定版本）、`current_version`（技能库当前版本）、`withdrawn`（已装版本是否已撤回）和 `update_available`。它会联网，只在用户要求检查更新时运行；其他命令不会在后台检查更新。

有新版本时，结果里的 `update.install_command` 是 Plaiers 安装器（`install-capability-skill.mjs`）安装该锁定版本的命令。**先征得用户同意再运行**；安装器会备份旧副本，失败时自动回滚。本 Skill 不会改写自己的文件，`update apply` / `update rollback` 已移除。

已装版本被撤回时，照实告知用户；当前副本保持不变，不会被自动删除，是否替换或移除由用户决定。不是通过技能库安装的副本（如仓库 `install.py` 的本地安装）返回 `not_registry_install`，需在仓库根目录 `git pull` 后重新运行安装器。
