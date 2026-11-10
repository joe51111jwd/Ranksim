"""Competitor lists: quoted CSV survives, non-sites are rejected with a reason, IDNA names normalize."""

from aeo.ranksim.space import competitor_host, parse_competitor_text, read_competitors, write_competitors


def test_quoted_csv_round_trips(tmp_path):
    path = tmp_path / "competitors.csv"
    write_competitors(path, ["acme.com", "b2w.example"], "file")
    assert read_competitors(path) == ["acme.com", "b2w.example"]
    pasted = 'domain,note\n"https://www.HCSS.com/heavyjob","Heavy, civil"\nraken.com,"field, app"\n'
    assert parse_competitor_text(pasted) == ["hcss.com", "raken.com"]


def test_non_sites_are_rejected_with_reasons():
    rejected = []
    text = "\n".join([
        "127.0.0.1",
        "http://10.0.0.5/",
        "[::1]",
        "localhost",
        "intranet.local",
        "acme.com:8080",
        "ftp://files.acme.com",
        "user:pw@acme.com",
        "nodot",
        "bad_host.com",
        "-acme.com",
        "good.com",
    ])
    assert parse_competitor_text(text, rejected) == ["good.com"]
    reasons = {line: reason for line, _text, reason in rejected}
    assert len(rejected) == 11
    assert "IP address" in reasons[1] and "IP address" in reasons[2] and "IP address" in reasons[3]
    assert "local network" in reasons[5] and "port" in reasons[6] and "scheme" in reasons[7]
    assert "credentials" in reasons[8] and "dot" in reasons[9] and "valid host" in reasons[10]


def test_international_names_are_stored_in_idna_form():
    assert competitor_host("https://bücher.de/shop") == ("xn--bcher-kva.de", "")
    assert competitor_host("www.Acme.COM.") == ("acme.com", "")


def test_the_cli_prints_rejected_lines(tmp_path, capsys):
    from aeo.ranksim.cli import main

    listing = tmp_path / "in.txt"
    listing.write_text("acme.com\n192.168.1.1\n")
    main(["--database", f"sqlite:///{tmp_path / 'c.db'}", "competitors", "import", "--file", str(listing), "--out", str(tmp_path / "out.csv")])
    out = capsys.readouterr().out
    assert "Rejected" in out and "192.168.1.1" in out and "IP address" in out
    assert (tmp_path / "out.csv").read_text().splitlines()[1].startswith("acme.com")
