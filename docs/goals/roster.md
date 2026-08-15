# 成员名册与生命周期(ROS)

`src/roster/`:成员是谁、怎么启动、怎么免权限弹窗、怎么活着。v0 的对应物是 `start.sh` 里的 ROSTER 数组。

- [x] **ROS-001** — `roster.toml` 配置文件:每个成员声明 `name`(= tmux 会话名)、`command`、`args`、`env`、`开场白模板`、`启用与否`;提供 schema 校验和加载 API;`start.sh` 改为读它的薄入口。
  - 前置:无。
  - 验证:`uv run ruff check . && uv run pytest tests/test_roster_load.py -q`
  - 证据:`roster.toml` + `src/roster/{schema,load,start,__main__}.py`;`start.sh` 改为 `uv run python -m roster`;`tests/test_roster_load.py` 11 passed(四成员加载、路径注入、停用排除、保留名/重名/缺字段/类型错误、start.sh 不再内嵌 ROSTER 数组)。
- [ ] **ROS-002** — 四个真实成员的适配档案落进 roster.toml:claude(`--permission-mode acceptEdits` + 项目级 `./msg` 白名单)、codex(`-s workspace-write -a on-request`)、cursor(`agent --force`)、agy(`--dangerously-skip-permissions -i`);每家的免弹窗方式和残留弹窗写进注释。
  - 处理登记:cursor,2026-08-16 06:28 +0800,`ros-002-cursor`。
  - 前置:ROS-001。
- [ ] **ROS-003** — 生命周期 API:`up/down/restart` 单成员与全体,幂等(已在跑的不重复拉起);启动时注入 `AGENT_NAME` 环境变量并发送开场白。
  - 前置:ROS-001、TMX-001。
- [ ] **ROS-004** — 健康检查与自动拉起:成员 `dead` 时按配置决定自动 respawn(默认关)或仅告警;连续 3 次拉起失败进入 `failed` 状态停止重试。
  - 前置:ROS-003、TMX-006。
- [ ] **ROS-005** — 收编存量会话:发现不在 roster 里的 tmux 会话,支持一键收编为临时成员(可收消息、上时间线),重启不保留。
  - 前置:ROS-003。
- [ ] **ROS-006** — 群规单一事实来源:成员运行时协议只维护在 `AGENTS.md`「群聊协议」一节,开场白由它生成;写一个一致性检查(测试或脚本)防止两处漂移。
  - 前置:ROS-001。
