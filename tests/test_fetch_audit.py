"""ARFF→CSV parsing (offline) and the audit module importing/running cleanly."""
from sentinel.datasets.fetch import _arff_to_csv

_ARFF = """@relation creditcard
@attribute Time numeric
@attribute V1 numeric
@attribute Amount numeric
@attribute Class {'0','1'}
@data
0,-1.359,149.62,'0'
406,-2.312,0.00,'1'
472,1.191,529.00,'0'
"""


def test_arff_to_csv(tmp_path):
    out = tmp_path / "cc.csv"
    n, fraud = _arff_to_csv(_ARFF, out)
    assert n == 3 and fraud == 1
    lines = out.read_text().strip().splitlines()
    assert lines[0] == "Time,V1,Amount,Class"
    assert lines[2] == "406,-2.312,0.00,1"        # quotes stripped


def test_audit_smoke(trained_model, capsys):
    # keep it cheap: monkeypatch the world size down
    import sentinel.audit as audit
    orig = audit._run_world
    audit._run_world = lambda eng, n_cust=40, days=20, seed=None: orig(eng, 40, 15, seed)
    try:
        audit.main()
    finally:
        audit._run_world = orig
    out = capsys.readouterr().out
    assert "LATENCY" in out and "FAIRNESS" in out and "txn/s/core" in out
