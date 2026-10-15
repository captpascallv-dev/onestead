"""共享文件夹：只给点名的伙伴只读搜索；不跟随链接、不碰登录资料和凭据文件、不让路径跳出文件夹。"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from onestead import config, folders, research
from onestead.store import BaseError


@pytest.fixture
def shared(tmp_path, monkeypatch):
    root = tmp_path / "调研"
    outside = tmp_path / "outside"
    (root / "报告").mkdir(parents=True)
    outside.mkdir()
    (outside / "机密.md").write_text("外面的暗号橙子", encoding="utf-8")
    (root / "报告" / "结论.md").write_text("市场结论：暗号蓝莓。", encoding="utf-8")
    (root / "报告" / "网页.html").write_text("<html><script>x=1</script><p>暗号&amp;葡萄</p></html>", encoding="utf-8")
    (root / "gbk.txt").write_bytes("旧编码暗号柠檬".encode("gb18030"))
    (root / "工具.py").write_text("暗号蓝莓", encoding="utf-8")
    (root / "secret-notes.md").write_text("暗号蓝莓", encoding="utf-8")
    (root / "app.local.json").write_text('{"x": "暗号蓝莓"}', encoding="utf-8")
    (root / ".env").write_text("暗号蓝莓", encoding="utf-8")
    (root / "带凭据.md").write_text("api_key = sk-abcdefghijklmnopqrstuvwxyz123456 暗号凭据", encoding="utf-8")
    browser = root / "采集" / "native" / "user-data"
    browser.mkdir(parents=True)
    (browser / "Local State").write_text("{}", encoding="utf-8")
    (browser / "notes.md").write_text("暗号蓝莓", encoding="utf-8")
    (root / "采集" / "chrome-profile").mkdir()
    (root / "采集" / "chrome-profile" / "a.md").write_text("暗号蓝莓", encoding="utf-8")
    (root / "排除我").mkdir()
    (root / "排除我" / "a.md").write_text("暗号蓝莓", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "a.md").write_text("暗号蓝莓", encoding="utf-8")
    linked = False
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(root / "跳出去"))
        linked = True
    else:
        os.symlink(outside, root / "跳出去")
        linked = True
    rows = [{"key": "research", "name": "调研", "path": str(root), "readers": ["chatgpt"], "exclude": ["排除我"]}]
    monkeypatch.setenv("ONESTEAD_SHARED_FOLDERS", json.dumps(rows, ensure_ascii=False))
    return SimpleNamespace(root=root, outside=outside, linked=linked)


def _rels():
    folder = folders.folders_for("chatgpt")[0]
    return sorted(rel for rel, _, _ in folders.walk(folder))


def test_walk_keeps_only_whitelisted_files(shared):
    assert _rels() == ["gbk.txt", "带凭据.md", "报告/结论.md", "报告/网页.html"]


def test_only_readers_see_the_folder(shared):
    assert folders.search("chatgpt", ["蓝莓"])
    assert folders.search("claude", ["蓝莓"]) == []
    assert folders.search(None, ["蓝莓"]) == []
    assert folders.fetch("claude", "folder:research/报告/结论.md") is None


def test_search_hits_body_and_path(shared):
    titles = [row["title"] for row in folders.search("chatgpt", ["蓝莓"])]
    assert titles == ["调研/报告/结论.md"]
    assert [row["title"] for row in folders.search("chatgpt", ["报告", "葡萄"])] == ["调研/报告/网页.html"]
    assert [row["title"] for row in folders.search("chatgpt", ["柠檬"])] == ["调研/gbk.txt"]
    assert folders.search("chatgpt", ["橙子"]) == []


@pytest.mark.parametrize("rel", [
    "../outside/机密.md", "报告/../../outside/机密.md", "跳出去/机密.md", "/etc/passwd", "C:/Windows/win.ini",
    "报告\\结论.md", "工具.py", "secret-notes.md", "app.local.json", ".env", "排除我/a.md", "node_modules/a.md",
    "采集/native/user-data/notes.md", "采集/chrome-profile/a.md", "报告/./结论.md", "报告//结论.md",
])
def test_fetch_refuses_paths_outside_the_rules(shared, rel):
    assert folders.fetch("chatgpt", f"folder:research/{rel}") is None


def test_fetch_reads_and_strips_html(shared):
    row = folders.fetch("chatgpt", "folder:research/报告/网页.html")
    assert "暗号&葡萄" in row["text"] and "x=1" not in row["text"]


def test_office_files_are_extracted(shared):
    import docx
    import openpyxl
    from pptx import Presentation

    d = docx.Document()
    d.add_paragraph("Word 暗号西瓜")
    d.save(shared.root / "报告" / "a.docx")
    p = Presentation()
    slide = p.slides.add_slide(p.slide_layouts[1])
    slide.shapes.title.text = "PPT 暗号桃子"
    p.save(shared.root / "报告" / "b.pptx")
    wb = openpyxl.Workbook()
    wb.active.append(["Excel", "暗号芒果"])
    wb.save(shared.root / "报告" / "c.xlsx")
    for word, name in (("西瓜", "a.docx"), ("桃子", "b.pptx"), ("芒果", "c.xlsx")):
        assert [r["title"] for r in folders.search("chatgpt", [word])] == [f"调研/报告/{name}"]
        assert word in folders.fetch("chatgpt", f"folder:research/报告/{name}")["text"]


def test_research_fetch_redacts_and_hides(shared):
    base = SimpleNamespace(_ctx=lambda: (SimpleNamespace(key="chatgpt", known=True, valid=True), [], []))
    out = research.fetch(base, "folder:research/带凭据.md", "https://base.example.test")
    assert "暗号凭据" in out.text and "sk-abcdefghijklmnopqrstuvwxyz123456" not in out.text
    assert out.url.startswith("https://base.example.test/#/folder/")
    with pytest.raises(BaseError):
        research.fetch(base, "folder:research/../outside/机密.md", "https://base.example.test")
    other = SimpleNamespace(_ctx=lambda: (SimpleNamespace(key="claude", known=True, valid=True), [], []))
    with pytest.raises(BaseError):
        research.fetch(other, "folder:research/报告/结论.md", "https://base.example.test")


@pytest.mark.parametrize("rows", [
    {"key": "x"}, [{"key": "x", "name": "n", "path": "p"}],
    [{"key": "x", "name": "n", "path": "p", "readers": []}],
    [{"key": "X Y", "name": "n", "path": "p", "readers": ["a"]}],
    [{"key": "x", "name": "n", "path": "p", "readers": ["a"]}, {"key": "x", "name": "m", "path": "q", "readers": ["a"]}],
    [{"key": "x", "name": "n", "path": "p", "readers": ["a"], "exclude": "nope"}],
])
def test_bad_config_is_rejected(monkeypatch, rows):
    monkeypatch.setenv("ONESTEAD_SHARED_FOLDERS", json.dumps(rows))
    with pytest.raises(BaseError):
        config.shared_folders()
