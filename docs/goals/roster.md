# 成员名册与生命周期(ROS)

`src/roster/`:成员是谁、怎么启动、怎么免权限弹窗、怎么活着。v0 的对应物是 `start.sh` 里的 ROSTER 数组。

- [x] **ROS-001** — `roster.toml` 配置文件:每个成员声明 `name`(= tmux 会话名)、`command`、`args`、`env`、`开场白模板`、`启用与否`;提供 schema 校验和加载 API;`start.sh` 改为读它的薄入口。
  - 前置:无。
  - 验证:`uv run ruff check . && uv run pytest tests/test_roster_load.py -q`
  - 证据:`roster.toml` + `src/roster/{schema,load,start,__main__}.py`;`start.sh` 改为 `uv run python -m roster`;`tests/test_roster_load.py` 11 passed(四成员加载、路径注入、停用排除、保留名/重名/缺字段/类型错误、start.sh 不再内嵌 ROSTER 数组)。
- [x] **ROS-002** — 四个真实成员的适配档案落进 roster.toml:claude(`--permission-mode acceptEdits` + 项目级 `./msg` 白名单)、codex(`-s workspace-write -a on-request`)、cursor(`agent --force`)、agy(`--dangerously-skip-permissions -i`);每家的免弹窗方式和残留弹窗写进注释。
  - 前置:ROS-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_roster_profiles.py tests/test_roster_load.py -q`
  - 证据:`roster.toml` 四成员参数与免弹窗/残留弹窗注释;`.claude/settings.json` 放行 `Bash(./msg *)`;`tests/test_roster_profiles.py` 钉住 flags、注释关键词与白名单,连同 ROS-001 共 14 passed。
- [x] **ROS-003** — 生命周期 API:`up/down/restart` 单成员与全体,幂等(已在跑的不重复拉起);启动时注入 `AGENT_NAME` 环境变量并发送开场白。
  - 前置:ROS-001、TMX-001。
  - 验证:`uv run ruff check . && uv run pytest tests/test_roster_lifecycle.py -q && uv run roster up`
  - 证据:`src/roster/lifecycle.py`(`Lifecycle.up/down/restart` 单个或全体,回 `LifecycleResult.changed` 说明这次动没动;`up` 已在跑就跳过、已停用就跳过,`down` 含已停用成员防残留会话,`restart` 对没在跑的直接拉起)、`src/roster/__main__.py` 加 `up|down|restart [名字]` 子命令并保留 v0 的 `start.sh` / `stop` / `<名字>` 三种用法、`pyproject.toml` 加 `roster` 命令入口;`tests/test_roster_lifecycle.py` 7 passed(假 tmux 覆盖幂等/单成员/未知成员/停用成员,外加真起一个临时 tmux 会话截画面确认 `AGENT_NAME=<name>` 与开场白都到了终端里);全量回归 147 passed。
  - 实测:2026-08-16 对在跑的四个真实成员执行 `uv run roster up`,输出四行「已在运行,跳过」,退出码 0,`tmux ls` 会话创建时间未变——幂等对真实成员成立,没有顶掉任何正在干活的 CLI。
- [ ] **ROS-004** — 健康检查与自动拉起:成员 `dead` 时按配置决定自动 respawn(默认关)或仅告警;连续 3 次拉起失败进入 `failed` 状态停止重试。
  - 前置:ROS-003、TMX-006。
- [ ] **ROS-005** — 收编存量会话:发现不在 roster 里的 tmux 会话,支持一键收编为临时成员(可收消息、上时间线),重启不保留。
  - 前置:ROS-003。
- [ ] **ROS-006** — 群规单一事实来源:成员运行时协议只维护在 `AGENTS.md`「群聊协议」一节,开场白由它生成;写一个一致性检查(测试或脚本)防止两处漂移。
  - 前置:ROS-001。
