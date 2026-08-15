# 四成员协作实测

QA-005 不用假成员：它连接名册里的 `claude/codex/cursor/agy` 四个真实
tmux 会话和仓库默认 bus。测试内容必须只读、带唯一 tag，避免与正在
进行的开发任务混淆。

## 实测流程

1. 记下 `bus/log.jsonl` 的下一行号，再用独立 tmux 会话起真实 console：

   ```bash
   wc -l bus/log.jsonl
   tmux new-session -d -s qa005-console -x 160 -y 40 -c "$PWD" 'uv run console'
   ```

2. 从 console 输入框给协调者派一个带 tag 的只读任务，要求它给其他三人
   分工，收齐后再给 `human` 一条总结。全程从时间线观察消息和成员状态。
3. 汇报完成后，用成员栏和 F5 对一个已空闲成员做软打断，再按 Esc 收起
   详情；时间线必须出现 `[控制] ✓ interrupt`，审计里必须有 `control`。
4. 从本轮起始行存档截取物和经 tag 过滤的标准审计事件：

   ```bash
   uv run python -m qa.collab archive --session qa005-console --start-line 60
   uv run python -m qa.collab verify
   ```

5. 按 `q` 退出 console，再比对 `tmux list-sessions`：四个成员会话必须仍在且
   `session_created` 没变。

## 2026-08-16 实测任务

标记 `QA005-20260816-A`。`human` 请 Claude 协调：Codex 核对 `uv run console`
启动入口，Cursor 核对 `t` 主题键，Agy 核对 F5 打断键，Claude 自查
`?`/F1 帮助；四人均只读，Claude 收齐后向 `human` 汇总。实测使用的
截图与审计片段由 `qa.collab verify` 离线检查，因此后续验收不需要再打扰
真实成员。

实测收到 20 条标准 `deposit/deliver` 事件，覆盖 `human→claude`、Claude
向三人派活、三人回报 Claude 以及 `claude→human` 汇总的 8 条必需有向边；
另有 1 条 `human→cursor` 提醒的入队/投递记录。汇报后从成员栏对空闲
Cursor 执行 F5，审计为 `control/interrupt/changed=true`，画面上同时可见成员
状态、完整协作链和成功控制反馈。退出 console 后的四个会话创建时间与开始前
一致：`claude=1786827034`，`codex/cursor/agy=1786827035`。
