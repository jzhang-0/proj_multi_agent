"""QA-004:CON 卷的证据必须引用真实存在的截取物。

这条规则光写在文档里没人守得住(截图很容易"忘了存"或"路径写错"),所以
用一个测试钉死:每个已经打 `[x]` 的 CON Goal,证据里至少引用一个
`tests/baseline/` 下真实存在的文件。
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_GOALS = REPO_ROOT / "docs" / "goals" / "console.md"
VISUAL_DOC = REPO_ROOT / "docs" / "quality" / "visual-check.md"

#: `- [x] **CON-002** — ...` 开头,直到下一个 `- [` 顶格条目
GOAL_BLOCK = re.compile(r"^- \[(?P<done>[ x])\] \*\*(?P<id>CON-\d+)\*\*(?P<body>.*?)(?=^- \[|\Z)",
                        re.MULTILINE | re.DOTALL)

BASELINE_REF = re.compile(r"tests/baseline/[\w.\-]+")

#: 纯性能/无界面变化的 Goal 可以显式声明视觉例外,免于引用截取物;例外
#: 必须带原因,不能只写"视觉例外"四个字就跳过。
VISUAL_EXEMPTION = re.compile(r"视觉例外\([^)]+\)[:：]\s*\S")


def finished_console_goals():
    text = CONSOLE_GOALS.read_text(encoding="utf-8")
    return [
        (match.group("id"), match.group("body"))
        for match in GOAL_BLOCK.finditer(text)
        if match.group("done") == "x"
    ]


def test_visual_check_doc_exists_and_is_indexed():
    assert VISUAL_DOC.is_file()
    index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "quality/visual-check.md" in index

    checklist = VISUAL_DOC.read_text(encoding="utf-8")
    for heading in ("对齐", "配色", "状态徽标", "中文宽度", "尺寸", "退出安全"):
        assert f"### {heading}" in checklist


@pytest.mark.parametrize("goal_id,body", finished_console_goals())
def test_finished_console_goal_cites_real_capture(goal_id, body):
    if VISUAL_EXEMPTION.search(body):
        return  # 纯性能/无界面变化,已显式声明例外并带原因

    refs = BASELINE_REF.findall(body)
    assert refs, f"{goal_id} 已完成但证据里没有引用 tests/baseline/ 下的截取物"
    for ref in refs:
        assert (REPO_ROOT / ref).is_file(), f"{goal_id} 引用的截取物不存在: {ref}"
