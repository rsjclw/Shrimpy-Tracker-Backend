"""recognize deployed cycle prediction inputs revision

Revision ID: 0013_cycle_prediction_inputs
Revises: 0012_blind_feeding_target_abw
Create Date: 2026-06-06

"""
from typing import Sequence, Union

revision: str = "0013_cycle_prediction_inputs"
down_revision: Union[str, None] = "0012_blind_feeding_target_abw"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
