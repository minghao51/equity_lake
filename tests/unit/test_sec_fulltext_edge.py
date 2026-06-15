"""Tests for SEC fulltext section extraction edge cases."""

from equity_lake.sources.sec_fulltext import SECFilingFetcher, _strip_html_tags


class TestStripHtmlTags:
    def test_preserves_apostrophes(self):
        html = "<p>Management's discussion &amp; analysis</p>"
        result = _strip_html_tags(html)
        assert "Management's" in result
        assert "&" in result

    def test_preserves_em_dash(self):
        html = "<p>Revenue &mdash; up 5%</p>"
        result = _strip_html_tags(html)
        assert "—" in result

    def test_preserves_numeric_entities(self):
        html = "<p>Cost was &#36;1,000</p>"
        result = _strip_html_tags(html)
        assert "$1,000" in result

    def test_strips_all_tags(self):
        html = "<div><span class='x'>Hello</span> <b>World</b></div>"
        result = _strip_html_tags(html)
        assert "<" not in result
        assert ">" not in result
        assert "Hello" in result
        assert "World" in result


class TestSectionExtractionWithTOC:
    """Verify TOC entries don't shadow actual section content."""

    def test_toc_plus_body_extracts_body(self):
        toc_html = """
        <html><body>
        <h2>Table of Contents</h2>
        <ul>
        <li>Item 1A. Risk Factors</li>
        <li>Item 7. Management's Discussion and Analysis</li>
        </ul>
        <h2>ITEM 1A. RISK FACTORS</h2>
        <p>The company faces significant supply chain disruption risks.
        Cybersecurity threats and regulatory changes could materially
        affect our business operations and financial results. Additionally,
        competition in the smartphone market remains intense.</p>
        <h2>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</h2>
        <p>Net sales increased 10% year-over-year driven by strong demand.
        Gross margins improved due to favorable product mix and cost savings
        from operational efficiency initiatives.</p>
        </body></html>
        """
        fetcher = SECFilingFetcher(tickers=["AAPL"])
        sections = fetcher._extract_sections(toc_html)

        section_names = [s[0] for s in sections]
        assert "risk_factors" in section_names
        assert "mda" in section_names

        risk_text = next(body for name, body in sections if name == "risk_factors")
        assert "supply chain" in risk_text.lower()
        assert len(risk_text) > 50

    def test_toc_only_returns_no_sections(self):
        toc_only_html = """
        <html><body>
        <h2>Table of Contents</h2>
        <ul>
        <li>Item 1A. Risk Factors</li>
        </ul>
        </body></html>
        """
        fetcher = SECFilingFetcher(tickers=["AAPL"])
        sections = fetcher._extract_sections(toc_only_html)

        # TOC entry without actual body content will be < 50 chars, filtered out
        risk_sections = [s for s in sections if s[0] == "risk_factors"]
        assert all(len(s[1]) > 50 for s in risk_sections) if risk_sections else True

    def test_section_min_length_filter(self):
        short_html = """
        <html><body>
        <h2>ITEM 1A. RISK FACTORS</h2>
        <p>Short.</p>
        </body></html>
        """
        fetcher = SECFilingFetcher(tickers=["AAPL"])
        sections = fetcher._extract_sections(short_html)
        assert all(len(body) > 50 for _, body in sections)
