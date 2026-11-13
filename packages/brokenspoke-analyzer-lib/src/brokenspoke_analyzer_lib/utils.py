"""Define utility functions."""

import gzip
import hashlib
import pathlib
import typing
import zipfile
from enum import Enum

import geopandas as gpd
from loguru import logger
from slugify import slugify

# WGS 84 / Pseudo-Mercator -- Spherical Mercator.
# https://epsg.io/3857
PSEUDO_MERCATOR_CRS = "EPSG:3857"


def unzip(
    zip_file: pathlib.Path,
    output_dir: pathlib.Path,
    *,
    delete_after: bool | None = True,
) -> None:
    """Unzip an archive into a specific directory."""
    # Decompress it.
    zip_file = zip_file.resolve(strict=True)
    with zipfile.ZipFile(zip_file) as zipped:
        zipped.extractall(output_dir.resolve(strict=True))

    # Delete the archive.
    if delete_after:
        zip_file.unlink()


def gunzip(
    gzip_file: pathlib.Path,
    target: pathlib.Path,
    *,
    delete_after: bool | None = True,
) -> None:
    """Gunzip a file into a specific target."""
    # Decompress it.
    gzip_file = gzip_file.resolve(strict=True)
    logger.debug(f"Decompressing {gzip_file} to {target}")
    with gzip.open(gzip_file, "rb") as f:
        try:
            content = f.read()
        except gzip.BadGzipFile as e:
            delete_after = True
            raise RuntimeError(
                f"Bad Gzip file: {gzip_file}. Try downloading it again.",
            ) from e
        except EOFError as e:
            delete_after = True
            raise RuntimeError(
                f"End of file error: {gzip_file}. Try downloading it again.",
            ) from e
        else:
            target.resolve().write_bytes(content)
        finally:
            if delete_after:
                gzip_file.unlink(missing_ok=False)


def file_checksum_ok(osm_file: pathlib.Path, osm_file_md5: pathlib.Path) -> bool:
    """Validate a file checksum."""
    buf_size = 65536
    hash_size = 32  # 128 bit MD5 hash
    md5 = hashlib.md5(usedforsecurity=False)

    with osm_file.open("rb") as f:
        while data := f.read(buf_size):
            md5.update(data)

    checksum = osm_file_md5.read_text()[:hash_size]

    return md5.hexdigest() == checksum


def normalize_unicode_name(value: str, separator: str = " ") -> str:
    """
    Normalize unicode names.

    Examples:
        >>> normalize_unicode_name("Québec")
        quebec

        >>> normalize_unicode_name("Cañon City")
        canon city
    """
    return slugify(value, save_order=True, separator=separator)


def get_srid(shapefile: pathlib.Path) -> int:
    """Get the SRID of a shapefile."""
    gdf = gpd.read_file(shapefile.resolve(strict=True))
    utm = gdf.estimate_utm_crs()
    return int(str(utm.to_string()[5:]))


def normalize_country_name(country: str) -> str:
    """
    Normalize the country name.

    The main goal of this function is to normalize the name of the United States
    of America. Since the country can have 3 names, it can be confusing. As per
    the BNA guidelines, the name of this country must be "united states".

    Examples:
        >>> normalize_country_name("US")
        united states

        >>> normalize_country_name("USA")
        united states

        >>> normalize_country_name("France")
        France

    """
    if is_usa(country):
        return "united states"
    return country


def is_usa(country: str) -> bool:
    """
    Return True if the country name is an alias of the USA.

    Examples:
        >>> is_usa('US')
        True

        >>> is_usa('USA')
        True

        >>> is_usa('United States')
        True

        >>> is_usa('France')
        False

    """
    return country.upper() in ["US", "USA", "UNITED STATES"]
