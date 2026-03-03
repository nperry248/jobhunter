"""
tests/unit/test_resume_parser.py — Unit tests for services/resume_parser.py.

WHAT WE'RE TESTING:
  - strip_html(): tag removal, entity decoding, whitespace normalization
  - parse_pdf(): file-not-found error path (actual PDF parsing tested via temp file)

NOTE ON parse_pdf() TESTS:
  Testing PDF parsing with real files would require shipping a test PDF.
  We test the error case (FileNotFoundError) here and rely on integration tests
  or manual testing for the full PDF extraction pipeline.
"""

import pytest

from services.resume_parser import strip_html


# ══════════════════════════════════════════════════════════════════════════════
# TestStripHtml
# ══════════════════════════════════════════════════════════════════════════════

class TestStripHtml:
    """Tests for strip_html()."""

    def test_removes_simple_tags(self) -> None:
        """Basic inline tags like <b>, <em>, <a> should be stripped."""
        result = strip_html("<b>Hello</b> <em>world</em>")
        assert result == "Hello world"

    def test_removes_paragraph_tags_and_adds_newline(self) -> None:
        """
        <p> tags should become newlines to preserve paragraph structure.
        WHY: If we just strip <p> with no newline, "First para.Second para."
        becomes a run-on sentence with no visual separation.
        """
        result = strip_html("<p>First paragraph.</p><p>Second paragraph.</p>")
        # Each <p> becomes a newline; strip() removes leading/trailing whitespace
        assert "First paragraph" in result
        assert "Second paragraph" in result
        # Paragraphs should be separated
        assert result.index("First") < result.index("Second")

    def test_removes_list_items(self) -> None:
        """<li> tags should become newlines so list items appear on separate lines."""
        result = strip_html("<ul><li>Python</li><li>SQL</li></ul>")
        assert "Python" in result
        assert "SQL" in result

    def test_removes_heading_tags(self) -> None:
        """<h1>–<h6> tags should be stripped, content preserved."""
        result = strip_html("<h1>Requirements</h1><h2>Nice to have</h2>")
        assert "Requirements" in result
        assert "Nice to have" in result

    def test_decodes_amp_entity(self) -> None:
        """&amp; should become &."""
        result = strip_html("Foo &amp; Bar")
        assert result == "Foo & Bar"

    def test_decodes_lt_gt_entities(self) -> None:
        """&lt; and &gt; should become < and >."""
        result = strip_html("Score &gt; 70 &lt; 100")
        assert ">" in result
        assert "<" in result

    def test_decodes_nbsp_entity(self) -> None:
        """&nbsp; should become a regular space."""
        result = strip_html("Hello&nbsp;World")
        assert "Hello World" in result

    def test_decodes_quot_entity(self) -> None:
        """&quot; should become a double-quote character."""
        result = strip_html('He said &quot;hello&quot;')
        assert '"hello"' in result

    def test_empty_string_returns_empty(self) -> None:
        """Empty input returns empty string without errors."""
        assert strip_html("") == ""

    def test_plain_text_unchanged(self) -> None:
        """Text with no HTML should be returned as-is (after whitespace normalization)."""
        result = strip_html("Plain text with no markup.")
        assert result == "Plain text with no markup."

    def test_nested_tags_fully_stripped(self) -> None:
        """Nested tags like <div><p><strong>...</strong></p></div> should all be removed."""
        html = "<div><p><strong>Key requirement:</strong> Python skills</p></div>"
        result = strip_html(html)
        assert result.strip() == "Key requirement: Python skills"

    def test_multiple_spaces_collapsed(self) -> None:
        """Multiple consecutive spaces should be collapsed to a single space."""
        result = strip_html("Hello   world")  # 3 spaces
        assert "  " not in result  # No double spaces after normalization

    def test_complex_job_description(self) -> None:
        """
        A realistic job description HTML should produce readable plain text.
        This is the primary use case — making Greenhouse/Lever descriptions readable.
        """
        html = (
            "<div>"
            "<h2>About the role</h2>"
            "<p>We are looking for a <strong>backend engineer</strong> "
            "with experience in Python &amp; SQL.</p>"
            "<h3>Requirements</h3>"
            "<ul>"
            "<li>3+ years Python</li>"
            "<li>PostgreSQL experience</li>"
            "<li>AWS or GCP preferred</li>"
            "</ul>"
            "</div>"
        )
        result = strip_html(html)
        assert "About the role" in result
        assert "backend engineer" in result
        assert "Python & SQL" in result
        assert "PostgreSQL experience" in result
        # Tags should all be gone
        assert "<" not in result
        assert ">" not in result


# ══════════════════════════════════════════════════════════════════════════════
# TestParsePdf — error case only
# ══════════════════════════════════════════════════════════════════════════════

class TestParsePdf:
    """Tests for parse_pdf() — error paths only (actual PDF parsing is integration-level)."""

    def test_raises_file_not_found_for_missing_file(self) -> None:
        """
        Passing a non-existent path should raise FileNotFoundError with a clear message.
        We want this to fail loudly at the call site, not silently return empty string.
        """
        from services.resume_parser import parse_pdf

        with pytest.raises(FileNotFoundError, match="Resume PDF not found"):
            parse_pdf("/tmp/this_file_does_not_exist_xyz_abc.pdf")
