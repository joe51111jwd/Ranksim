from aeo.ranksim.saved_html import load_saved_html


def test_saved_html_is_used_and_missing_files_are_not_invented(tmp_path):
    page = tmp_path / "g2.html"
    page.write_text("<html><title>Saved G2 page</title><body><p>HCSS alternatives.</p></body></html>")
    (tmp_path / "manifest.csv").write_text(
        "url,file\n"
        "https://www.g2.com/products/hcss/competitors/alternatives,g2.html\n"
        "https://www.reddit.com/r/estimators/comments/11jjop2/estimating_software_options/,missing.html\n"
    )
    found, missing = load_saved_html(tmp_path)
    assert "Saved G2 page" in found["https://www.g2.com/products/hcss/competitors/alternatives"]
    assert missing == ["https://www.reddit.com/r/estimators/comments/11jjop2/estimating_software_options/"]
    assert "https://www.reddit.com/r/estimators/comments/11jjop2/estimating_software_options/" not in found


def test_saved_html_rejects_paths_outside_the_directory(tmp_path):
    (tmp_path / "manifest.csv").write_text("url,file\nhttps://example.com/a,../secret.html\n")
    try:
        load_saved_html(tmp_path)
    except ValueError as exc:
        assert "inside the directory" in str(exc)
    else:
        raise AssertionError("path traversal was accepted")
