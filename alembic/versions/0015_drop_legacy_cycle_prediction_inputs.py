"""drop legacy cycle prediction input columns

Revision ID: 0015_drop_legacy_cycle_prediction_inputs
Revises: 0014_cycle_prediction_config
Create Date: 2026-06-06

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015_drop_legacy_cycle_prediction_inputs"
down_revision: Union[str, None] = "0014_cycle_prediction_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_COLUMNS = (
    "prediction_preparation_day",
    "preparation_day",
    "prediction_maximum_shrimp_size_g",
    "maximum_shrimp_size_g",
    "prediction_target_fcr",
    "target_fcr",
    "prediction_maximum_adg_g_per_day",
    "maximum_adg_g_per_day",
    "prediction_initial_feeding_index",
    "initial_feeding_index",
    "prediction_stable_carrying_capacity_kg_per_m2",
    "stable_carrying_capacity_kg_per_m2",
    "prediction_final_carrying_capacity_kg_per_m2",
    "final_carrying_capacity_kg_per_m2",
    "prediction_minimum_partial_harvest_biomass_kg",
    "minimum_partial_harvest_biomass_kg",
    "prediction_harvest_fixed_cost_per_event",
    "harvest_fixed_cost_per_event",
    "prediction_harvest_price_points",
    "harvest_price_points",
    "prediction_pl_price_per_piece",
    "pl_price_per_piece",
    "prediction_electricity_kwh",
    "electricity_kwh",
    "prediction_electricity_price_per_kwh",
    "electricity_price_per_kwh",
    "prediction_labor_cost_per_day",
    "labor_cost_per_day",
    "prediction_probiotics_cost_per_day",
    "probiotics_cost_per_day",
    "prediction_disinfection_cost_per_day",
    "disinfection_cost_per_day",
    "prediction_liming_cost_per_day",
    "liming_cost_per_day",
    "prediction_feed_plan",
    "feed_plan",
)


def upgrade() -> None:
    for column_name in LEGACY_COLUMNS:
        op.execute(f'ALTER TABLE cycles DROP COLUMN IF EXISTS "{column_name}"')


def downgrade() -> None:
    pass
