"""Script per accoreconsole."""
from etcad import batch

def test_script_saveas():
    s = batch.script_saveas_dwg("C:/tmp/x.dwg")
    assert "SAVEAS" in s and "2018" in s and "x.dwg" in s
