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

NB_SIGCTL_SEARCH_DIST = 25
NB_PBL_BUFFER_M = 25
NB_PBL_MAX_ANGLE_DEG = 15
NB_PBL_MAX_GAP_M = 400
NB_PBL_MAX_GAP_DIST_M = 40


def execute_sql_file_shim(
    engine: Engine,
    sql_directory: pathlib.Path,
    sql_file: str,
    bind_params: typing.Mapping[str, typing.Any] | None = None,
) -> None:
    """Shim to allow partial func creation of the execute_sql_file function."""
    dbcore.execute_sql_file(engine, sql_directory / sql_file, bind_params)


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
    bind_params = {"nb_output_srid": output_srid}
    execute_sql("prepare_tables.sql", bind_params)

    # Clip.
    logger.info("Clip OSM source data to boundary + buffer")
    bind_params = {"nb_boundary_buffer": boundary_buffer}
    execute_sql("clip_osm.sql", bind_params)

    # Remove paths that prohibit bicycles.
    logger.info("Removing paths that prohibit bicycles")
    dbcore.execute_query(
        engine,
        "DELETE FROM neighborhood_osm_full_line WHERE bicycle='no' and highway='path';",
    )

    # Setting values on road segments.
    logger.info("Setting values on road segments")
    sql_scripts: list[tuple[str, dict[str, typing.Any] | None]] = [
        ("one_way.sql", None),
        ("width_ft.sql", None),
        ("functional_class.sql", None),
        ("paths.sql", {"nb_output_srid": output_srid}),
        ("speed_limit.sql", None),
        ("lanes.sql", None),
        ("park.sql", None),
        (
            "bike_infra.sql",
            {
                "pbl_buffer_m": NB_PBL_BUFFER_M,
                "pbl_max_angle_deg": NB_PBL_MAX_ANGLE_DEG,
                "pbl_max_gap_m": NB_PBL_MAX_GAP_M,
                "pbl_max_gap_dist_m": NB_PBL_MAX_GAP_DIST_M,
            },
        ),
        ("class_adjustments.sql", None),
        ("legs.sql", None),
        ("signalized.sql", {"sigctl_search_dist": NB_SIGCTL_SEARCH_DIST}),
        ("stops.sql", {"sigctl_search_dist": NB_SIGCTL_SEARCH_DIST}),
        ("rrfb.sql", {"sigctl_search_dist": NB_SIGCTL_SEARCH_DIST}),
        ("island.sql", {"sigctl_search_dist": NB_SIGCTL_SEARCH_DIST}),
    ]
    for script, bind_params in sql_scripts:
        dbcore.execute_sql_file(
            engine, sql_script_dir / "features" / script, bind_params
        )


def stress(
    engine: Engine,
    sql_script_dir: pathlib.Path,
    state_default_speed: int | None,
    city_default_speed: int | None,
) -> None:
    """Compute stress levels."""
    # Calculating stress.
    logger.info("Calculating stress")

    # {Log Description: (script_name, bind_params | None)}
    sql_scripts: dict[str, tuple[str, dict[str, typing.Any] | None]] = {
        "Motorway/Trunk routes": ("stress_motorway-trunk.sql", None),
        "Primary routes": (
            "stress_segments_higher_order.sql",
            {
                "class": "primary",
                "default_speed": 40,
                "default_lanes": 2,
                "default_parking": 1,
                "default_parking_width": 8,
                "default_facility_width": 5,
            },
        ),
        "Secondary routes": (
            "stress_segments_higher_order.sql",
            {
                "class": "secondary",
                "default_speed": 40,
                "default_lanes": 2,
                "default_parking": 1,
                "default_parking_width": 8,
                "default_facility_width": 5,
            },
        ),
        "Tertiary routes": (
            "stress_segments_higher_order.sql",
            {
                "class": "tertiary",
                "default_speed": 30,
                "default_lanes": 1,
                "default_parking": 1,
                "default_parking_width": 8,
                "default_facility_width": 5,
            },
        ),
        "Residential routes": (
            "stress_segments_lower_order_res.sql",
            {
                "class": "residential",
                "default_lanes": 1,
                "default_parking": 1,
                "default_roadway_width": 27,
                "state_default": state_default_speed,
                "city_default": city_default_speed,
            },
        ),
        "Unclassified routes": (
            "stress_segments_lower_order.sql",
            {
                "class": "unclassified",
                "default_speed": 25,
                "default_lanes": 1,
                "default_parking": 1,
                "default_roadway_width": 27,
            },
        ),
        "Living Streets": ("stress_living_street.sql", None),
        "Cycle Tracks": ("stress_track.sql", None),
        "Cycle Paths": ("stress_path.sql", None),
        "Correcting One-ways": ("stress_one_way_reset.sql", None),
        "Motorway/Trunk intersections": ("stress_motorway-trunk_ints.sql", None),
        "Primary intersections": ("stress_primary_ints.sql", None),
        "Secondary intersections": ("stress_secondary_ints.sql", None),
        "Tertiary intersections": (
            "stress_tertiary_ints.sql",
            {
                "primary_speed": 40,
                "secondary_speed": 40,
                "primary_lanes": 2,
                "secondary_lanes": 2,
            },
        ),
        "Residential/unclassified intersections": (
            "stress_lesser_ints.sql",
            {
                "primary_speed": 40,
                "secondary_speed": 40,
                "tertiary_speed": 30,
                "primary_lanes": 2,
                "secondary_lanes": 2,
                "tertiary_lanes": 1,
            },
        ),
        "Other intersections": ("stress_link_ints.sql", None),
    }

    for log_description, (sql_script, bind_params) in sql_scripts.items():
        logger.info(log_description)
        dbcore.execute_sql_file(
            engine, sql_script_dir / "stress" / sql_script, bind_params
        )


@dataclass(frozen=True)
class BlockRoad:
    """Define the Block Road items."""

    # Buffer distance to find roads associated with a block.
    buffer: int = 15
    # Minimum length road must overlap with block buffer to be associated .
    min_length: int = 30


@dataclass(frozen=True)
class Score:
    """Define the Score parts."""

    total: int = 100
    people: int = 15
    opportunity: int = 20
    core_services: int = 20
    retail: int = 15
    recreation: int = 15
    transit: int = 15


@dataclass(frozen=True)
class StepwiseScoring:
    """Define the Stepwise Scoring parts for population and jobs access."""

    max_score: int = 1
    step1: float = 0.03
    score1: float = 0.1
    step2: float = 0.2
    score2: float = 0.4
    step3: float = 0.5
    score3: float = 0.8


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


def connectivity(
    engine: Engine,
    sql_script_dir: pathlib.Path,
    output_srid: int,
    max_trip_distance: int = constant.DEFAULT_MAX_TRIP_DISTANCE,
    *,
    import_jobs: bool,
) -> None:
    """Compute BNA connectivity scores."""
    # Prepare computation variables.
    block_road = BlockRoad()
    score = Score()
    stepwise_scoring = StepwiseScoring()

    # Create partial function with the engine and directory pre-bound
    # to make calls to execute_sql_file less repetitive and more readable.
    execute_sql: typing.Callable[[str, dict | None], None] = partial(
        execute_sql_file_shim, engine, sql_script_dir / "connectivity"
    )

    # Building network.
    logger.info("BUILDING: Building network")
    bind_params = {"nb_output_srid": output_srid}
    execute_sql("build_network.sql", bind_params)

    bind_params = {
        "block_road_buffer": block_road.buffer,
        "block_road_min_length": block_road.min_length,
        "nb_output_srid": output_srid,
    }
    execute_sql("census_blocks.sql", bind_params)

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
    execute_sql("access_population.sql", bind_params=asdict(stepwise_scoring))

    # Import jobs.
    if import_jobs:
        logger.info("METRICS: Access: jobs")
        execute_sql("census_block_jobs.sql")
        execute_sql("access_jobs.sql", bind_params=asdict(stepwise_scoring))

    # Destinations.
    logger.info("METRICS: Destinations")
    destinations: list[Destination] = [
        Destination(name="colleges", tolerance=100, access=Access(0.7)),
        Destination(
            name="community_centers",
            tolerance=50,
            access=Access(0.4, 0.2, 0.1),
        ),
        Destination(
            name="doctors",
            tolerance=50,
            access=Access(0.4, 0.2, 0.1),
        ),
        Destination(
            name="dentists",
            tolerance=50,
            access=Access(0.4, 0.2, 0.1),
        ),
        Destination(name="hospitals", tolerance=50, access=Access(0.7)),
        Destination(
            name="pharmacies",
            tolerance=50,
            access=Access(0.4, 0.2, 0.1),
        ),
        Destination(name="parks", tolerance=50, access=Access(0.3, 0.2, 0.2)),
        Destination(
            name="retail",
            tolerance=50,
            access=Access(0.4, 0.2, 0.1),
        ),
        Destination(name="transit", tolerance=75, access=Access(0.6)),
        Destination(name="universities", tolerance=150, access=Access(0.7)),
        Destination(name="schools", access=Access(0.3, 0.2, 0.2)),
        Destination(name="social_services", access=Access(0.7)),
        Destination(name="supermarkets", access=Access(0.6, 0.2)),
    ]

    sql_destination_script_dir = sql_script_dir / "connectivity" / "destinations"
    for destination in destinations:
        bind_params = {"nb_output_srid": output_srid}
        if destination.tolerance is not None:
            bind_params["cluster_tolerance"] = destination.tolerance
        dbcore.execute_sql_file(
            engine,
            sql_destination_script_dir / f"{destination.name}.sql",
            bind_params,
        )

    # Add trails for access calculations.
    destinations.append(Destination(name="trails", access=TrailAccess(0.7, 0.2)))

    # Accesses.
    logger.info("METRICS: Accesses")
    for destination in destinations:
        execute_sql(
            f"access_{destination.name}.sql", bind_params=asdict(destination.access)
        )

    # Access_overall.
    execute_sql("access_overall.sql", bind_params=asdict(score))

    # Prepare score inputs.
    execute_sql("score_inputs.sql")

    # Overall score.
    execute_sql("overall_scores.sql", bind_params=asdict(score))

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
