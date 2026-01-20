from app.parsers.router import select_parser


def test_select_parser_prefers_content_type():
    assert select_parser("application/pdf", "https://example.com/index.html") == "pdf"


def test_select_parser_falls_back_to_extension():
    assert select_parser("", "https://example.com/report.xlsx") == "xlsx"


def test_select_parser_defaults_to_html():
    assert select_parser("application/octet-stream", "https://example.com/data") == "html"
