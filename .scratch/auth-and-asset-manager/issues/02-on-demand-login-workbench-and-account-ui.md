# 02: On-Demand Login Workbench & Top-Bar Account Management UI

**What to build:** Add an interactive Login and Registration page to the Local UI Workbench that launches automatically whenever any authenticated CLI command or workbench action finds missing, expired, or unrefreshable credentials (or when `login` is invoked explicitly), saves the resulting session credentials, provisions the user's partner identity, seamlessly resumes the interrupted CLI command, and displays active account and quota status in the workbench top bar.

**Blocked by:** 01: Identity Service & Asset Gateway Dual-Protocol Client with Auto-Refresh

**Status:** ready-for-agent

- [ ] Running any authenticated Asset Skill command when `token` and `refreshToken` are missing—or when `POST /api/user/refresh` fails with an authentication error—automatically starts the local Login Workbench and opens the browser (unless `--no-login-ui` or a headless environment is active, which fails fast with a `need_login` error payload).
- [ ] Explicit `login` and `logout` CLI subcommands are available across all Asset Skills to proactively sign in, switch accounts, or clear local credentials.
- [ ] The Login Workbench page provides Login and Registration tabs, real-time username availability checking (`^[a-z0-9_]{3,32}$`), password validation (`6–64` characters), optional nickname input, and an expandable setting to view or change the Identity Service URL.
- [ ] Completing login or registration in the browser persists `{ login_svr_url, token, refreshToken, asynx, user }`, calls `GET /v1/partner/identity`, shuts down the standalone login server, and seamlessly resumes the interrupted CLI command to completion without requiring the user or AI Agent to re-run it.
- [ ] Local UI Workbenches display the signed-in user (`nickname` / `username`), remaining USD budget, and active/maximum task concurrency in the top navigation bar, with actions to change password, switch account, or log out.
- [ ] End-to-end workbench HTTP tests verify username checking, registration, login, password change, logout, and automatic resumption of a blocked CLI task.
