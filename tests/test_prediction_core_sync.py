"""Guard: app/services/prediction_core.py must stay byte-identical to the
source-of-truth simulation at <repo>/simulation/predict.py.

The simulation lives in the parent superproject, outside this submodule, so it is
not always checked out (e.g. CI that clones the submodule alone). When the source
is unreachable the test skips rather than failing.

To re-sync after changing the simulation:
    Copy-Item ../simulation/predict.py app/services/prediction_core.py -Force
"""
from pathlib import Path

import pytest

VENDORED = Path(__file__).resolve().parents[1] / "app" / "services" / "prediction_core.py"
# tests -> backend root -> Shrimpy-Tracker (superproject) -> simulation/predict.py
SOURCE = Path(__file__).resolve().parents[2] / "simulation" / "predict.py"


def _normalized(path: Path) -> str:
    # Compare content, not line endings: the two files live in different repos whose
    # git autocrlf settings may differ, so a raw byte compare is fragile on checkout.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_prediction_core_matches_simulation_source():
    if not SOURCE.exists():
        pytest.skip(f"source-of-truth simulation not checked out at {SOURCE}")
    assert _normalized(VENDORED) == _normalized(SOURCE), (
        "app/services/prediction_core.py has drifted from simulation/predict.py. "
        "Re-sync with: Copy-Item ../simulation/predict.py app/services/prediction_core.py -Force"
    )
