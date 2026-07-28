"""fetch_paper / transcribe_paper——路径闸、PDF 校验、转写落盘。

下载用 _download 打桩（零真网络）；转写用 pymupdf 现造的迷你 PDF（零外部依赖）。
_PROJECT_ROOT 重定向 tmp_path，不碰真 knowledge/。
"""

from __future__ import annotations

import pymupdf
import pytest

import src.tools.paper as paper_mod
from src.tools.paper import fetch_paper, transcribe_paper

_PDF_MAGIC = b"%PDF-1.7 fake body "


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    import src.paths as paths

    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(paper_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(paper_mod, "KNOWLEDGE_DIR", tmp_path / "knowledge")


def _fake_pdf_bytes(size: int = 60_000) -> bytes:
    return _PDF_MAGIC + b"x" * (size - len(_PDF_MAGIC))


class TestFetchPaper:
    def test_arxiv_id_maps_to_pdf_url_and_saves(self, monkeypatch, tmp_path):
        seen = {}
        def fake_dl(url):
            seen["url"] = url
            return _fake_pdf_bytes()
        monkeypatch.setattr(paper_mod, "_download", fake_dl)
        result = fetch_paper("2401.12345", "knowledge/paper/test/paper.pdf")
        assert "✅" in result
        assert seen["url"] == "https://arxiv.org/pdf/2401.12345.pdf"
        assert (tmp_path / "knowledge/paper/test/paper.pdf").read_bytes().startswith(b"%PDF")

    def test_rejects_non_https_and_bad_source(self, monkeypatch):
        monkeypatch.setattr(paper_mod, "_download", lambda _u: _fake_pdf_bytes())
        assert "❌" in fetch_paper("http://evil.com/x.pdf", "knowledge/a.pdf")
        assert "❌" in fetch_paper("ftp://x/y.pdf", "knowledge/a.pdf")
        assert "❌" in fetch_paper("not-an-id", "knowledge/a.pdf")

    def test_dest_must_be_inside_knowledge_pdf(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paper_mod, "_download", lambda _u: _fake_pdf_bytes())
        assert "❌" in fetch_paper("2401.12345", "src/evil.pdf")
        assert "❌" in fetch_paper("2401.12345", "knowledge/paper/x/paper.txt")
        assert "❌" in fetch_paper("2401.12345", "../outside/paper.pdf")
        assert not (tmp_path / "src" / "evil.pdf").exists()

    def test_rejects_error_page_masquerading_as_pdf(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paper_mod, "_download",
                            lambda _u: b"<html>404 not found</html>" * 3000)
        result = fetch_paper("2401.12345", "knowledge/paper/x/paper.pdf")
        assert "不是有效 PDF" in result
        assert not (tmp_path / "knowledge/paper/x/paper.pdf").exists(), "坏内容不落盘"

    def test_rejects_tiny_download(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paper_mod, "_download", lambda _u: b"%PDF-1.7 tiny")
        assert "不是有效 PDF" in fetch_paper("2401.12345", "knowledge/paper/x/paper.pdf")

    def test_download_failure_returns_message_not_raise(self, monkeypatch):
        def boom(_u):
            raise OSError("connection refused")
        monkeypatch.setattr(paper_mod, "_download", boom)
        result = fetch_paper("2401.12345", "knowledge/paper/x/paper.pdf")
        assert "下载失败" in result and "connection refused" in result


class TestTranscribePaper:
    def _make_real_pdf(self, tmp_path, rel="knowledge/paper/t/paper.pdf",
                       texts=("Abstract: hello", "Conclusion: world")) -> str:
        pdf = tmp_path / rel
        pdf.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        for t in texts:
            page = doc.new_page()
            page.insert_text((72, 72), t)
        doc.save(str(pdf))
        doc.close()
        return rel

    def test_transcribes_pages_with_headers(self, tmp_path):
        rel = self._make_real_pdf(tmp_path)
        result = transcribe_paper(rel, "knowledge/paper/t/paper.md")
        assert "✅" in result and "2 页" in result
        md = (tmp_path / "knowledge/paper/t/paper.md").read_text(encoding="utf-8")
        assert "## Page 1" in md and "## Page 2" in md
        assert "Abstract: hello" in md and "Conclusion: world" in md

    def test_src_and_dest_path_gates(self, tmp_path):
        rel = self._make_real_pdf(tmp_path)
        assert "❌" in transcribe_paper(rel, "src/paper.md")
        assert "❌" in transcribe_paper(rel, "knowledge/paper/t/paper.txt")
        assert "❌" in transcribe_paper("knowledge/paper/nope/paper.pdf",
                                       "knowledge/paper/nope/paper.md")

    def test_corrupt_pdf_returns_message_not_raise(self, tmp_path):
        bad = tmp_path / "knowledge/paper/bad/paper.pdf"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"not a pdf at all")
        result = transcribe_paper("knowledge/paper/bad/paper.pdf",
                                  "knowledge/paper/bad/paper.md")
        assert "❌" in result
