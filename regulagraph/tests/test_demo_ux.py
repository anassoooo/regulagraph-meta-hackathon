"""Demo UX wording: the footer says quotations are exact as extracted, not as printed."""

from app.main import WEB_DIR

FOOTER = "Quotations are reproduced exactly as extracted from the official PDFs."


def test_footer_states_quotations_are_exact_as_extracted() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert html.count(FOOTER) == 1
    assert "Quotations are reproduced exactly," not in html  # the earlier wording, which implied the printed text
    footer = html[html.index('<footer class="footer">'):html.index("</footer>")]
    assert FOOTER in footer and 'href="https://www.bct.gov.tn/" target="_blank" rel="noopener noreferrer"' in footer
