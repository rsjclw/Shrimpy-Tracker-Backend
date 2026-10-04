"""A cycle's results, as the past-cycles cards show them (Shrimpy-Tracker#9).

Pure: the cycles router gathers the rows and the per-day standing biomass and
passes them in. The numbers agree with the last day's metrics and the finish
check - same harvests, same feed, same end day.
"""

from dataclasses import dataclass, field
from datetime import date as ddate, time as dtime
from decimal import Decimal

from app.services import metrics as M


@dataclass(frozen=True)
class HarvestIn:
    date: ddate
    harvest_time: dtime
    biomass_kg: Decimal
    abw_g: Decimal
    count: int
    revenue: Decimal


@dataclass(frozen=True)
class HarvestLine:
    date: ddate
    doc: int
    harvest_time: dtime
    biomass_kg: Decimal
    abw_g: Decimal
    # Size: shrimp per kg at the harvest's ABW.
    size_pcs_per_kg: int | None
    count: int
    revenue: Decimal
    # On the cycle's last day: part of the final population.
    final: bool


@dataclass
class CycleSummary:
    ended: bool
    end_date: ddate
    doc: int
    area_m2: Decimal | None
    total_harvest_kg: Decimal
    total_feed_kg: Decimal
    # What the harvests sold for, all of them up to the last day.
    total_revenue: Decimal
    # Total harvest over pond area: t per 1,000 m2, which is the same number as kg/m2.
    yield_t_per_1000m2: Decimal | None
    fcr: Decimal | None
    survival_rate_pct: Decimal | None
    initial_population: int
    final_population: int | None
    # Biomass of the final harvest: the last day's harvests.
    final_harvest_kg: Decimal | None
    harvested_count: int
    final_abw_g: Decimal | None
    # Final ABW over total DOC.
    average_adg_g_per_day: Decimal | None
    max_biomass_kg: Decimal | None
    max_biomass_doc: int | None
    # Max standing biomass over pond area, kg/m2.
    max_carrying_capacity_kg_m2: Decimal | None
    highest_daily_feed_kg: Decimal | None
    highest_daily_feed_doc: int | None
    max_mortality: int | None
    max_mortality_doc: int | None
    harvests: list[HarvestLine] = field(default_factory=list)


def _per_area(value: Decimal | None, area_m2: Decimal | None) -> Decimal | None:
    if value is None or not area_m2 or area_m2 <= 0:
        return None
    return (value / area_m2).quantize(Decimal("0.01"))


def cycle_summary(
    *,
    start_date: ddate,
    initial_population: int,
    area_m2: Decimal | None,
    end_date: ddate,
    ended: bool,
    feedings: list[M.FeedingRow],
    harvests: list[M.HarvestRow],
    harvest_details: list[HarvestIn],
    abw_history: list[M.AbwRow],
    mortality: dict[ddate, int],
    daily_biomass: dict[ddate, Decimal | None],
) -> CycleSummary:
    doc = lambda d: M.doc_for(start_date, d)  # noqa: E731
    in_cycle = [h for h in harvest_details if h.date <= end_date]
    total_harvest = sum((h.biomass_kg for h in in_cycle), Decimal("0"))
    total_feed = M.cumulative_feed_kg(feedings, end_date)

    samples = [a for a in abw_history if a.date <= end_date]
    final_abw = max(samples, key=lambda a: a.sampled_at).abw_g if samples else None
    end_doc = doc(end_date)

    biomass = [(d, b) for d, b in daily_biomass.items() if d <= end_date and b is not None]
    top_biomass = max(biomass, key=lambda x: x[1], default=None)

    feed_by_day: dict[ddate, Decimal] = {}
    for f in feedings:
        if f.date <= end_date:
            feed_by_day[f.date] = feed_by_day.get(f.date, Decimal("0")) + f.amount_kg
    top_feed = max(feed_by_day.items(), key=lambda x: x[1], default=None)

    deaths = [(d, n) for d, n in mortality.items() if d <= end_date]
    top_deaths = max(deaths, key=lambda x: x[1], default=None)

    return CycleSummary(
        ended=ended,
        end_date=end_date,
        doc=end_doc,
        area_m2=area_m2,
        total_harvest_kg=total_harvest,
        total_feed_kg=total_feed,
        total_revenue=sum((h.revenue for h in in_cycle), Decimal("0")),
        yield_t_per_1000m2=_per_area(total_harvest, area_m2),
        fcr=(total_feed / total_harvest).quantize(Decimal("0.01")) if total_harvest > 0 else None,
        survival_rate_pct=M.survival_rate_pct(initial_population, harvests, end_date) if ended else None,
        initial_population=initial_population,
        final_population=M.final_population(harvests, end_date) if ended else None,
        final_harvest_kg=sum((h.biomass_kg for h in in_cycle if h.date == end_date), Decimal("0")) if ended else None,
        harvested_count=M.harvested_count(harvests, end_date),
        final_abw_g=final_abw,
        average_adg_g_per_day=(final_abw / end_doc).quantize(Decimal("0.01")) if final_abw is not None and end_doc > 0 else None,
        max_biomass_kg=top_biomass[1].quantize(Decimal("0.1")) if top_biomass else None,
        max_biomass_doc=doc(top_biomass[0]) if top_biomass else None,
        max_carrying_capacity_kg_m2=_per_area(top_biomass[1], area_m2) if top_biomass else None,
        highest_daily_feed_kg=top_feed[1] if top_feed else None,
        highest_daily_feed_doc=doc(top_feed[0]) if top_feed else None,
        max_mortality=top_deaths[1] if top_deaths else None,
        max_mortality_doc=doc(top_deaths[0]) if top_deaths else None,
        harvests=[
            HarvestLine(
                date=h.date,
                doc=doc(h.date),
                harvest_time=h.harvest_time,
                biomass_kg=h.biomass_kg,
                abw_g=h.abw_g,
                size_pcs_per_kg=round(1000 / h.abw_g) if h.abw_g > 0 else None,
                count=h.count,
                revenue=h.revenue,
                final=ended and h.date == end_date,
            )
            for h in sorted(in_cycle, key=lambda h: (h.date, h.harvest_time))
        ],
    )
