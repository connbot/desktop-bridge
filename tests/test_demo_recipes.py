"""Keep the public fictional-data recipe and the real capture fixture in sync."""
import json
import runpy
from pathlib import Path


def test_fictional_spending_recipe(tmp_path):
    source = Path(__file__).parents[1] / "demo" / "personal-workspace"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    script = tmp_path / "scripts" / "analyze.py"
    script.write_bytes((source / "analyze.py").read_bytes())
    (tmp_path / "data" / "sample-spending.csv").write_bytes(
        (source / "sample-spending.csv").read_bytes()
    )
    runpy.run_path(str(script))
    result = json.loads((tmp_path / "outputs" / "spending-summary.json").read_text())
    assert result["total"] == 126.4
    assert len(result["transactions"]) == 5
    assert result["categories"] == {"Groceries": 72.4, "Transport": 24.0, "Home": 30.0}
    assert "fictional" in result["data_classification"]
    text = (tmp_path / "outputs" / "spending-summary.md").read_text()
    assert "$126.40" in text
    assert "Fictional data only" in text
