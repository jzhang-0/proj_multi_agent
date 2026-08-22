# 运行时提示词

这里是 amux 成员启动提示的唯一事实来源，正文不要复制进 Python、`AGENTS.md`、团队档案或名册。

启动时按以下顺序拼装：

1. `common.md`：所有成员都有的身份、通信、留痕和安全约束。
2. `leader.md`：仅 `role = "leader"` 的成员追加。
3. `member.md`：仅 `role = "member"` 的成员追加。

没有团队角色的自定义成员只使用 `common.md`。可用占位符由 `roster.protocol` 校验，修改提示词后运行 `uv run pytest tests/test_roster_protocol.py tests/test_team_activation.py tests/test_release_package.py -q`。

| 文件 | 可用占位符 |
|---|---|
| `common.md` | `{name}`、`{workspace_slug}`、`{project_root}` |
| `leader.md` | `{team_id}`、`{model}`、`{responsibility}`、`{team_roster}` |
| `member.md` | `{team_id}`、`{leader_name}`、`{model}`、`{responsibility}` |

占位符由团队档案和当前工作区动态填充。规则正文写在 Markdown，团队成员名单、模型和职责继续写在 `~/.amux/teams/<id>.toml`，不要把具体团队硬编码进提示词。
