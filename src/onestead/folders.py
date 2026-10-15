"""共享文件夹：把主人在配置里点名的本机文件夹，只读地开放给指定伙伴搜索和读正文。

- 只认配置 shared_folders 里的文件夹，只给 readers 里的身份看；不进网页、同事视图和公开页面。
- 只读白名单类型的文件；跳过符号链接和目录联接、浏览器登录资料、带凭据字样的文件和目录。
- 读文件时从文件夹根逐级核对，路径里不许有 ..、盘符或链接，解析后的真实路径也必须还在文件夹里。
- PDF、Word、PPT、Excel 抽出文字；正文出去前仍走 safe_text 遮掉凭据和本机路径。
"""

from __future__ import annotations

import html
import logging
import os
import re
import threading
from fnmatch import fnmatch
from pathlib import Path

from . import config
from .i18n import text as tr

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # 字体解析的提示每页一条，会刷满日志

TEXT_TYPES = {".md", ".txt", ".csv", ".json", ".html", ".htm"}
DOC_TYPES = {".pdf", ".docx", ".pptx", ".xlsx"}
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_DOC_BYTES = 50 * 1024 * 1024
MAX_CHARS = 200_000  # 一份正文最多给这么多字，超出的截断并注明
SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", "site-packages", ".idea", ".vscode"}
# 名字里带这些字样的文件或目录一律不碰（凭据、登录态）
SENSITIVE = re.compile(r"(?i)secret|token|credential|cookie|password|passwd|private[-_ ]?key|^\.env(\..*)?$|\.local\.|"
                       r"profile|login[-_ ]?data|^local state$|\.pem$|\.key$|\.pfx$|\.p12$")
# 浏览器资料目录里的标志文件：目录里有任何一个，整个目录跳过
BROWSER_MARKERS = {"Local State", "Cookies", "Login Data", "Web Data", "Preferences", "Secure Preferences"}

_cache: dict[str, tuple[int, int, str]] = {}
_cache_lock = threading.Lock()


def folders_for(viewer_key: str | None) -> list[dict]:
    if not viewer_key:
        return []
    return [f for f in config.shared_folders() if viewer_key in f["readers"]]


def _is_link(entry: os.DirEntry | Path) -> bool:
    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)
        return bool(junction and junction())
    except OSError:
        return True


def _excluded(rel: str, patterns: list[str]) -> bool:
    rel_l = rel.lower()
    for p in patterns:
        p = p.replace("\\", "/").strip("/").lower()
        if rel_l == p or rel_l.startswith(p + "/") or fnmatch(rel_l, p):
            return True
    return False


def _name_ok(name: str) -> bool:
    return not SENSITIVE.search(name)


def _file_ok(name: str, size: int) -> bool:
    ext = os.path.splitext(name)[1].lower()
    if not _name_ok(name):
        return False
    if ext in TEXT_TYPES:
        return size <= MAX_TEXT_BYTES
    return ext in DOC_TYPES and size <= MAX_DOC_BYTES


def walk(folder: dict):
    """逐个给出 (相对路径, 绝对路径, stat)；不跟随任何链接。"""
    root = Path(folder["path"])
    if not root.is_dir() or _is_link(root):
        return
    stack = [(root, "")]
    while stack:
        here, prefix = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        if any(e.name in BROWSER_MARKERS for e in entries):
            continue
        for e in entries:
            rel = f"{prefix}{e.name}"
            if _is_link(e) or not _name_ok(e.name) or _excluded(rel, folder["exclude"]):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append((Path(e.path), rel + "/"))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    if _file_ok(e.name, st.st_size):
                        yield rel, Path(e.path), st
            except OSError:
                continue


def resolve(folder: dict, rel: str) -> Path | None:
    """把相对路径安全地落回文件夹里的一个文件；任何一步不合规就返回 None。"""
    if not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")  # 不做规整：只认 walk 给出的标准写法
    if any(p in ("", ".", "..") or p != p.strip() for p in parts):
        return None
    if _excluded(rel, folder["exclude"]):
        return None
    root = Path(folder["path"])
    if _is_link(root):
        return None
    here = root
    for i, part in enumerate(parts):
        if not _name_ok(part) or (i < len(parts) - 1 and part in SKIP_DIRS):
            return None
        try:
            if any((here / m).exists() for m in BROWSER_MARKERS):
                return None
        except OSError:
            return None
        here = here / part
        if _is_link(here):
            return None
    try:
        real, real_root = here.resolve(strict=True), root.resolve(strict=True)
        st = here.stat()
    except OSError:
        return None
    if not real.is_relative_to(real_root) or not here.is_file() or not _file_ok(here.name, st.st_size):
        return None
    return here


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _html_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(text))


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    out = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            out.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(out)


def _pptx(path: Path) -> str:
    from pptx import Presentation

    out = []
    for n, slide in enumerate(Presentation(str(path)).slides, 1):
        out.append(tr("folder.page", n=n))
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                out.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    out.append("\t".join(cell.text for cell in row.cells))
        if slide.has_notes_slide:
            out.append(slide.notes_slide.notes_text_frame.text)
    return "\n".join(out)


def _xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    out, total = [], 0
    try:
        for ws in wb.worksheets:
            out.append(tr("folder.sheet", name=ws.title))
            for row in ws.iter_rows(values_only=True):
                line = "\t".join("" if v is None else str(v) for v in row).rstrip()
                if line:
                    out.append(line)
                    total += len(line)
                if total > MAX_CHARS:
                    return "\n".join(out)
    finally:
        wb.close()
    return "\n".join(out)


EXTRACT = {".pdf": _pdf, ".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx}


def text_of(path: Path, st: os.stat_result | None = None) -> str:
    """抽出正文；按路径、修改时间、大小缓存在内存里。抽不出来返回空串。"""
    st = st or path.stat()
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    ext = path.suffix.lower()
    try:
        if ext in EXTRACT:
            text = EXTRACT[ext](path)
        else:
            text = _read_text(path)
            if ext in (".html", ".htm"):
                text = _html_text(text)
    except ImportError:
        text = ""
        log.warning("缺少抽取 %s 文字的库，装 onestead[docs] 后才能读", ext)
    except Exception:  # 坏文件、加密 PDF 等：当作读不出文字
        log.info("抽不出文字：%s", path.name, exc_info=True)
        text = ""
    with _cache_lock:
        _cache[key] = (st.st_mtime_ns, st.st_size, text)
    return text


def doc_id(folder: dict, rel: str) -> str:
    return f"folder:{folder['key']}/{rel}"


def parse_id(document_id: str) -> tuple[str, str] | None:
    if not document_id.startswith("folder:"):
        return None
    key, _, rel = document_id.removeprefix("folder:").partition("/")
    return (key, rel) if key and rel else None


def search(viewer_key: str | None, terms: list[str], limit: int = 50) -> list[dict]:
    """所有词都要出现在路径或正文里；路径命中的排前面。"""
    hits: list[tuple[int, dict]] = []
    for folder in folders_for(viewer_key):
        for rel, path, st in walk(folder):
            name = f"{folder['name']}/{rel}".lower()
            if all(t in name for t in terms):
                hits.append((0, _row(folder, rel, "")))
                continue
            body = text_of(path, st).lower()
            if all(t in name or t in body for t in terms):
                hits.append((1, _row(folder, rel, "")))
    hits.sort(key=lambda h: (h[0], h[1]["title"]))
    return [row for _, row in hits[:limit]]


def fetch(viewer_key: str | None, document_id: str) -> dict | None:
    parsed = parse_id(document_id)
    if not parsed:
        return None
    key, rel = parsed
    folder = next((f for f in folders_for(viewer_key) if f["key"] == key), None)
    path = resolve(folder, rel) if folder else None
    if path is None:
        return None
    text = text_of(path)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n" + tr("folder.truncated", n=MAX_CHARS)
    if not text.strip():
        text = tr("folder.no_text")
    return _row(folder, rel, text)


def _row(folder: dict, rel: str, text: str) -> dict:
    return {"id": doc_id(folder, rel), "title": f"{folder['name']}/{rel}", "text": text,
            "metadata": {"type": tr("folder.type"), "folder": folder["name"], "source": "Onestead"}}


def warm(viewer_keys: set[str] | None = None) -> None:
    """后台预先抽好文字，免得第一次搜索太慢。"""
    def run():
        for folder in config.shared_folders():
            if viewer_keys is not None and not (viewer_keys & set(folder["readers"])):
                continue
            for _, path, st in walk(folder):
                text_of(path, st)
        log.info("共享文件夹的文字已抽好")

    threading.Thread(target=run, daemon=True, name="shared-folders-warm").start()
