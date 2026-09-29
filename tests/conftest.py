import warnings

import numpy as np
import pytest

warnings.filterwarnings("ignore")


@pytest.fixture(autouse=True)
def _clean_feedback():
    """Keep tests from leaving a real feedback.jsonl that a later run would pick up."""
    from sentinel import config
    p = config.FEEDBACK_PATH
    before = p.read_bytes() if p.exists() else None
    yield
    if before is None and p.exists():
        p.unlink()
    elif before is not None:
        p.write_bytes(before)


@pytest.fixture(scope="session")
def small_world():
    """A small synthetic world + its (X, y, ts, cols) dataset."""
    from sentinel.datasets import SyntheticSource
    from sentinel.train import build_dataset

    src = SyntheticSource(90, 45, seed=7)
    events = src.events()
    customers = {c.cust_id: c for c in src.customers}
    X, y, ts, cols = build_dataset(events, customers)
    return dict(src=src, events=events, customers=customers, X=X, y=y, ts=ts, cols=cols)


@pytest.fixture(scope="session")
def trained_model(small_world):
    from sentinel.model import train
    from sentinel.train import temporal_split

    split = temporal_split(small_world["ts"])
    model, meta = train(small_world["X"], small_world["y"],
                        feature_names=small_world["cols"], split=split)
    return model, meta
