"""Тесты утилит парсинга fallback-режима CrawlClient."""
from deep_research.tools.crawl_client import _extract_title, _html_to_text


def test_extract_title():
    html = "<html><head><title>  Hello   World  </title></head><body></body></html>"
    assert _extract_title(html) == "Hello World"


def test_extract_title_missing():
    assert _extract_title("<html><body>no title</body></html>") == ""


def test_html_to_text_strips_scripts_and_styles():
    html = """
    <html>
      <head>
        <style>body { color: red; }</style>
        <script>alert('x')</script>
      </head>
      <body>
        <article>
          <h1>Title</h1>
          <p>First paragraph.</p>
          <p>Second & third.</p>
        </article>
      </body>
    </html>
    """
    out = _html_to_text(html)
    assert "First paragraph." in out
    assert "Second & third." in out
    assert "alert" not in out
    assert "color: red" not in out
    assert "Title" in out
