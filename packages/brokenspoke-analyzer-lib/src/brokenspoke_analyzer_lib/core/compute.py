"""
Define functions to run SQL scripts.

Define functions to run the various SQL scripts performing the operations to
compute the BNA scores.
"""

import pathlib
import typing
from dataclasses import (
    asdict,
    dataclass,
)
from functools import partial

from loguru import logger
from sqlalchemy.engine import Engine

from brokenspoke_analyzer_lib import (
    constant,
)
from brokenspoke_analyzer_lib.database import dbcore


def execute_sql_file_shim(
    engine: Engine,
    sql_directory: pathlib.Path,
    sql_file: str,
    bind_params: typing.Mapping[str, typing.Any] | None = None,
) -> None:
    """Shim to allow partial func creation of the execute_sql_file function."""
    dbcore.execute_sql_file(engine, sql_directory / sql_file, bind_params)


NB_PBL_SEARCH_VALS: typing.Final[dict[str, int]] = {
    "pbl_buffer_m": 25,
    "pbl_max_angle_deg": 15,
    "pbl_max_gap_m": 400,
    "pbl_max_gap_dist_m": 40,
}

NB_SIGCTL: typing.Final[dict[str, int]] = {
    "sigctl_search_dist": 25,
}


def features(
    engine: Engine,
    sql_script_dir: pathlib.Path,
    output_srid: int,
    boundary_buffer: int,
) -> None:
    """Compute the BNA features."""
    # Create partial function with the engine and directory pre-bound
    # to make calls to execute_sql_file less repetitive and more readable.
    execute_sql: typing.Callable[[str, dict | None], None] = partial(
        execute_sql_file_shim, engine, sql_script_dir
    )

    # Update field names.
    logger.info("Update field names")
    execute_sql("prepare_tables.sql", {"nb_output_srid": output_srid})

    # Clip.
    logger.info("Clip OSM source data to boundary + buffer")
    execute_sql("clip_osm.sql", {"nb_boundary_buffer": boundary_buffer})

    # Remove paths that prohibit bicycles.
    logger.info("Removing paths that prohibit bicycles")
    dbcore.execute_query(
        engine,
        "DELETE FROM neighborhood_osm_full_line WHERE bicycle='no' and highway='path';",
    )

    # Setting values on road segments.
    logger.info("Setting values on road segments and intersections")
    sql_scripts: tuple[tuple[str, dict[str, typing.Any] | None], ...] = (
        ("one_way.sql", None),
        ("width_ft.sql", None),
        ("functional_class.sql", None),
        ("paths.sql", {"nb_output_srid": output_srid}),
        ("speed_limit.sql", None),
        ("lanes.sql", None),
        ("park.sql", None),
        ("bike_infra.sql", NB_PBL_SEARCH_VALS),
        ("class_adjustments.sql", None),
        ("legs.sql", None),
        ("signalized.sql", NB_SIGCTL),
        ("stops.sql", NB_SIGCTL),
        ("rrfb.sql", NB_SIGCTL),
        ("island.sql", NB_SIGCTL),
    )
    for script, bind_params in sql_scripts:
        dbcore.execute_sql_file(
            engine, sql_script_dir / "features" / script, bind_params
        )


@dataclass(frozen=True)
class RoadDefaults:
    """Define the Road Class Defaults."""

    default_speed: int
    default_lanes: int
    default_facility_width: int = 5


PRIMARY_DEFAULTS: typing.Final[dict[str, str | int]] = {
    "class": "primary",
    **asdict(RoadDefaults(default_speed=40, default_lanes=2)),
}
SECONDARY_DEFAULTS: typing.Final[dict[str, str | int]] = {
    "class": "secondary",
    **asdict(RoadDefaults(default_speed=40, default_lanes=2)),
}
TERTIARY_DEFAULTS: typing.Final[dict[str, str | int]] = {
    "class": "tertiary",
    **asdict(RoadDefaults(default_speed=30, default_lanes=1)),
}

TERTIARY_INTS_DEFAULTS: typing.Final[dict[str, int]] = {
    "primary_speed": 40,
    "secondary_speed": 40,
    "primary_lanes": 2,
    "secondary_lanes": 2,
}
LESSER_INTS_DEFAULTS: typing.Final[dict[str, int]] = {
    **TERTIARY_INTS_DEFAULTS,
    "tertiary_speed": 30,
    "tertiary_lanes": 1,
}


def stress(
    engine: Engine,
    sql_script_dir: pathlib.Path,
    state_default_speed: int | None,
    city_default_speed: int | None,
) -> None:
    """Compute stress levels."""
    # Calculating stress.
    logger.info("Calculating stress")

    # (script_name, bind_params | None, description)
    sql_scripts: tuple[tuple[str, dict[str, typing.Any] | None, str], ...] = (
        ("stress_motorway-trunk.sql", None, "Motorway/Trunk routes"),
        ("stress_segments_higher_order.sql", PRIMARY_DEFAULTS, "Primary routes"),
        ("stress_segments_higher_order.sql", SECONDARY_DEFAULTS, "Secondary routes"),
        ("stress_segments_higher_order.sql", TERTIARY_DEFAULTS, "Tertiary routes"),
        (
            "stress_segments_lower_order_res.sql",
            {
                "class": "residential",
                "default_lanes": 1,
                "default_parking": 1,
                "default_roadway_width": 27,
                "state_default": state_default_speed,
                "city_default": city_default_speed,
            },
            "Residential routes",
        ),
        (
            "stress_segments_lower_order.sql",
            {
                "class": "unclassified",
                "default_speed": 25,
                "default_lanes": 1,
                "default_parking": 1,
                "default_roadway_width": 27,
            },
            "Unclassified routes",
        ),
        ("stress_living_street.sql", None, "Living Streets"),
        ("stress_track.sql", None, "Cycle Tracks"),
        ("stress_path.sql", None, "Cycle Paths"),
        ("stress_one_way_reset.sql", None, "Correcting One-ways"),
        ("stress_motorway-trunk_ints.sql", None, "Motorway/Trunk intersections"),
        ("stress_primary_ints.sql", None, "Primary intersections"),
        ("stress_secondary_ints.sql", None, "Secondary intersections"),
        ("stress_tertiary_ints.sql", TERTIARY_INTS_DEFAULTS, "Tertiary intersections"),
        ("stress_lesser_ints.sql", LESSER_INTS_DEFAULTS, "Lesser intersections"),
        ("stress_link_ints.sql", None, "Other intersections"),
    )

    for sql_script, bind_params, description in sql_scripts:
        logger.info(description)
        dbcore.execute_sql_file(
            engine, sql_script_dir / "stress" / sql_script, bind_params
        )


# Define the Block Road items.
BLOCK_ROAD: typing.Final[dict[str, int]] = {
    # Buffer distance to find roads associated with a block.
    "block_road_buffer": 15,
    # Minimum length road must overlap with block buffer to be associated.
    "block_road_min_length": 30,
}


# Define the Score parts.
SCORE: typing.Final[dict[str, int]] = {
    "total": 100,
    "people": 15,
    "opportunity": 20,
    "core_services": 20,
    "retail": 15,
    "recreation": 15,
    "transit": 15,
}


# Define the piecewise Scoring parts for population and jobs access.
PIECEWISE_SCORING: typing.Final[dict[str, int | float]] = {
    "max_score": 1,
    "step1": 0.3,
    "score1": 0.1,
    "step2": 0.2,
    "score2": 0.4,
    "step3": 0.5,
    "score3": 0.8,
}


@dataclass(frozen=True)
class Access:
    """Define the Access parts."""

    first: float = 0.0
    second: float = 0.0
    third: float = 0.0
    max_score: int = 1


@dataclass(frozen=True)
class TrailAccess(Access):
    """Inherited class to add the Path Constraints."""

    # Minimum path length to be considered for recreation access.
    min_path_length: int = 4800
    # Minimum corner-to-corner span of path bounding box to be considered for
    # recreation access.
    min_bbox_length: int = 3300


@dataclass(frozen=True, kw_only=True)
class Destination:
    """Define the Destination parts."""

    name: str
    tolerance: int | None = None
    access: Access


DESTINATIONS: typing.Final[tuple[Destination, ...]] = (
    Destination(name="colleges", tolerance=100, access=Access(first=0.7)),
    Destination(
        name="community_centers",
        tolerance=50,
        access=Access(first=0.4, second=0.2, third=0.1),
    ),
    Destination(
        name="doctors",
        tolerance=50,
        access=Access(first=0.4, second=0.2, third=0.1),
    ),
    Destination(
        name="dentists",
        tolerance=50,
        access=Access(first=0.4, second=0.2, third=0.1),
    ),
    Destination(name="hospitals", tolerance=50, access=Access(first=0.7)),
    Destination(
        name="pharmacies",
        tolerance=50,
        access=Access(first=0.4, second=0.2, third=0.1),
    ),
    Destination(
        name="parks", tolerance=50, access=Access(first=0.3, second=0.2, third=0.2)
    ),
    Destination(
        name="retail",
        tolerance=50,
        access=Access(first=0.4, second=0.2, third=0.1),
    ),
    Destination(name="transit", tolerance=75, access=Access(first=0.6)),
    Destination(name="universities", tolerance=150, access=Access(first=0.7)),
    Destination(name="schools", access=Access(first=0.3, second=0.2, third=0.2)),
    Destination(name="social_services", access=Access(first=0.7)),
    Destination(name="supermarkets", access=Access(first=0.6, second=0.2)),
)

# Separate trails beacuse they are scored but have no import script.
TRAIL_DESTINATION: typing.Final = Destination(
    name="trails", access=TrailAccess(first=0.7, second=0.2)
)


def connectivity(
    engine: Engine,
    sql_script_dir: pathlib.Path,
    output_srid: int,
    max_trip_distance: int = constant.DEFAULT_MAX_TRIP_DISTANCE,
    *,
    import_jobs: bool,
) -> None:
    """Compute BNA connectivity scores."""
    # Create partial function with the engine and directory pre-bound
    # to make calls to execute_sql_file less repetitive and more readable.
    execute_sql: typing.Callable[[str, dict | None], None] = partial(
        execute_sql_file_shim, engine, sql_script_dir / "connectivity"
    )

    # Building network.
    logger.info("BUILDING: Building network")
    execute_sql("build_network.sql", {"nb_output_srid": output_srid})
    execute_sql("census_blocks.sql", {**BLOCK_ROAD, "nb_output_srid": output_srid})

    # Build the temporary block verts for reachable roads calc
    logger.info("CONNECTIVITY: Block verts")
    execute_sql("block_verts.sql")

    # Reachable roads stress.
    for stress_level in ["high", "low"]:
        logger.info(f"CONNECTIVITY: Reachable roads {stress_level} stress")

        # Prep.
        logger.info(f"Reachable roads {stress_level} stress: prep")
        execute_sql(f"reachable_roads_{stress_level}_stress_prep.sql")

        # Calculations
        logger.info(f"Reachable roads {stress_level} stress: calculations")
        for i in range(8):
            bind_params = {
                "thread_num": 8,
                "thread_no": i,
                "nb_max_trip_distance": max_trip_distance,
            }
            execute_sql(
                f"reachable_roads_{stress_level}_stress_calc.sql",
                bind_params,
            )

        # Cleanup.
        logger.info(f"Reachable roads {stress_level} stress: cleanup")
        execute_sql(
            f"reachable_roads_{stress_level}_stress_cleanup.sql",
        )

    # Drop the temporary block verts
    dbcore.execute_query(
        engine, "DROP TABLE IF EXISTS generated.neighborhood_block_verts;"
    )

    # Connected census blocks.
    logger.info("CONNECTIVITY: Connected census blocks")
    bind_params = {
        "nb_max_trip_distance": max_trip_distance,
        "nb_output_srid": output_srid,
    }
    execute_sql("connected_census_blocks.sql", bind_params)

    # Access population
    logger.info("METRICS: Access: population")
    execute_sql("access_population.sql", bind_params=PIECEWISE_SCORING)

    # Import jobs.
    if import_jobs:
        logger.info("METRICS: Access: jobs")
        execute_sql("census_block_jobs.sql")
        execute_sql("access_jobs.sql", bind_params=PIECEWISE_SCORING)

    # Destinations.
    logger.info("METRICS: Destinations")
    sql_destination_script_dir = sql_script_dir / "connectivity" / "destinations"
    for destination in DESTINATIONS:
        bind_params = {"nb_output_srid": output_srid}
        if destination.tolerance is not None:
            bind_params["cluster_tolerance"] = destination.tolerance
        dbcore.execute_sql_file(
            engine,
            sql_destination_script_dir / f"{destination.name}.sql",
            bind_params,
        )

    # Accesses.
    logger.info("METRICS: Accesses")
    for destination in (*DESTINATIONS, TRAIL_DESTINATION):
        execute_sql(
            f"access_{destination.name}.sql", bind_params=asdict(destination.access)
        )

    # Access overall.
    execute_sql("access_overall.sql", bind_params=SCORE)

    # Prepare score inputs.
    execute_sql("score_inputs.sql")

    # Overall score.
    execute_sql("overall_scores.sql", bind_params=SCORE)

    # Block-level category scores.
    execute_sql("category_scores.sql")


def measure(
    engine: Engine,
    sql_script_dir: pathlib.Path,
) -> None:
    """Compute BNA mileage."""
    # Prepare the paths.
    sql_script_dir = sql_script_dir.resolve(strict=True)
    sql_connectivity_script_dir = sql_script_dir / "features"

    # Calculating mileage.
    logger.info("MILEAGE: Calculating mileage")
    sql_script = sql_connectivity_script_dir / "calculate_mileage.sql"
    dbcore.execute_sql_file(engine, sql_script)


def all_(
    database_url: str,
    sql_script_dir: pathlib.Path,
    output_srid: int,
    state_default_speed: int | None,
    city_default_speed: int | None,
    buffer: int = constant.DEFAULT_BUFFER,
    max_trip_distance: int = constant.DEFAULT_MAX_TRIP_DISTANCE,
    *,
    import_jobs: bool,
) -> None:
    """Compute all features."""
    parts(
        buffer=buffer,
        city_default_speed=city_default_speed,
        compute_parts=constant.COMPUTE_PARTS_ALL,
        database_url=database_url,
        import_jobs=import_jobs,
        max_trip_distance=max_trip_distance,
        output_srid=output_srid,
        sql_script_dir=sql_script_dir,
        state_default_speed=state_default_speed,
    )


def parts(
    *,
    database_url: str,
    sql_script_dir: pathlib.Path,
    output_srid: int,
    buffer: int = constant.DEFAULT_BUFFER,
    city_default_speed: int | None,
    compute_parts: list[constant.ComputePart] | None = constant.DEFAULT_COMPUTE_PARTS,
    import_jobs: bool,
    max_trip_distance: int = constant.DEFAULT_MAX_TRIP_DISTANCE,
    state_default_speed: int | None,
) -> None:
    """Cherry pick the parts of the analysis to compute."""
    # Make mypy happy.
    if not buffer:
        raise ValueError("`buffer` must be set")
    if not compute_parts:
        raise ValueError("`compute_parts` must be set")

    if not compute_parts:
        logger.info("no compute action was specified")

    # Prepare the database connection.
    engine = dbcore.create_psycopg_engine(database_url)

    # Compute features.
    # Features are required to compute ALL the other parts, therefore are being
    # run every time.
    logger.info("Compute features")
    features(engine, sql_script_dir, output_srid, buffer)

    # Compute stress.
    if constant.ComputePart.STRESS in compute_parts:
        logger.info("Compute stress")
        stress(
            engine,
            sql_script_dir,
            state_default_speed,
            city_default_speed,
        )

    # Compute connectivity.
    if constant.ComputePart.CONNECTIVITY in compute_parts:
        logger.info("Compute connectivity")
        connectivity(
            engine,
            sql_script_dir,
            output_srid,
            max_trip_distance,
            import_jobs=import_jobs,
        )

    # Compute mileage.
    if constant.ComputePart.MEASURE in compute_parts:
        logger.info("Compute mileage")
        measure(
            engine,
            sql_script_dir,
        )
