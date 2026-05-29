"""Configuration helpers for Databricks Auto Loader ingestion paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


MACHINE_EVENT_COLUMNS: Tuple[str, ...] = (
    "event_id",
    "machine_id",
    "event_ts",
    "site_id",
    "client_id",
    "model",
    "hour_meter",
    "event_type",
    "status",
    "fault_code",
    "severity",
    "temperature_c",
    "vibration_mm_s",
    "fuel_level_pct",
    "duration_minutes",
    "downtime_minutes",
    "maintenance_cost_gbp",
    "part_code",
    "part_quantity",
    "operator_shift",
)

DEFAULT_SOURCE_PATH = "dbfs:/FileStore/lakehouse_demo/raw_machine_events"
DEFAULT_CHECKPOINT_PATH = "dbfs:/FileStore/lakehouse_demo/_checkpoints/bronze_machine_events"
DEFAULT_SCHEMA_LOCATION = "dbfs:/FileStore/lakehouse_demo/_schemas/bronze_machine_events"

DEFAULT_AZURE_SOURCE_PATH = "lakehouse_demo/raw_machine_events"
DEFAULT_AZURE_CHECKPOINT_PATH = "lakehouse_demo/_checkpoints/bronze_machine_events"
DEFAULT_AZURE_SCHEMA_LOCATION = "lakehouse_demo/_schemas/bronze_machine_events"

DEFAULT_VOLUME_SOURCE_PATH = "raw_machine_events"
DEFAULT_VOLUME_CHECKPOINT_PATH = "_checkpoints/bronze_machine_events"
DEFAULT_VOLUME_SCHEMA_LOCATION = "_schemas/bronze_machine_events"


@dataclass(frozen=True)
class IngestionPaths:
    source_path: str
    checkpoint_path: str
    schema_location: str


@dataclass(frozen=True)
class AzureIngestionConfig:
    catalog: str = "main"
    schema: str = "lakehouse_demo"
    source_path: str = DEFAULT_SOURCE_PATH
    checkpoint_path: str = DEFAULT_CHECKPOINT_PATH
    schema_location: str = DEFAULT_SCHEMA_LOCATION
    unity_catalog_volume: str = ""
    volume_source_path: str = DEFAULT_VOLUME_SOURCE_PATH
    volume_checkpoint_path: str = DEFAULT_VOLUME_CHECKPOINT_PATH
    volume_schema_location: str = DEFAULT_VOLUME_SCHEMA_LOCATION
    azure_storage_account: str = ""
    azure_container: str = ""
    azure_source_path: str = DEFAULT_AZURE_SOURCE_PATH
    azure_checkpoint_path: str = DEFAULT_AZURE_CHECKPOINT_PATH
    azure_schema_location: str = DEFAULT_AZURE_SCHEMA_LOCATION
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def quote_sql_identifier(*parts: str) -> str:
    """Quote a multipart SQL identifier for Spark SQL."""
    clean_parts = [_clean(part) for part in parts]
    missing_parts = [index + 1 for index, part in enumerate(clean_parts) if not part]
    if missing_parts:
        raise ValueError(f"SQL identifier part {missing_parts[0]} is required")

    return ".".join(f"`{part.replace('`', '``')}`" for part in clean_parts)


def build_abfss_uri(container: str, storage_account: str, path: str) -> str:
    """Build an ADLS Gen2 abfss URI from bundle-friendly components."""
    clean_container = _clean(container)
    clean_account = _clean(storage_account)
    clean_path = _clean(path)

    if not clean_container:
        raise ValueError("azure_container is required for Azure ingestion paths")
    if not clean_account:
        raise ValueError("azure_storage_account is required for Azure ingestion paths")
    if clean_path.startswith("abfss://"):
        return clean_path.rstrip("/")

    uri_root = f"abfss://{clean_container}@{clean_account}.dfs.core.windows.net"
    if not clean_path:
        return uri_root

    return f"{uri_root}/{clean_path.strip('/')}"


def build_volume_path(catalog: str, schema: str, volume: str, path: str = "") -> str:
    """Build a Unity Catalog volume path for Spark reads and writes."""
    clean_catalog = _clean(catalog)
    clean_schema = _clean(schema)
    clean_volume = _clean(volume)
    clean_path = _clean(path)

    if not clean_catalog:
        raise ValueError("catalog is required for Unity Catalog volume paths")
    if not clean_schema:
        raise ValueError("schema is required for Unity Catalog volume paths")
    if not clean_volume:
        raise ValueError("unity_catalog_volume is required for Unity Catalog volume paths")

    if clean_path.startswith("dbfs:/Volumes/"):
        clean_path = clean_path.removeprefix("dbfs:")
    if clean_path.startswith("/Volumes/"):
        return clean_path.rstrip("/")

    volume_root = f"/Volumes/{clean_catalog}/{clean_schema}/{clean_volume}"
    if not clean_path:
        return volume_root

    return f"{volume_root}/{clean_path.strip('/')}"


def resolve_ingestion_paths(config: AzureIngestionConfig) -> IngestionPaths:
    """Resolve DBFS, Unity Catalog volume or Azure ADLS paths for bronze ingestion."""
    unity_catalog_volume = _clean(config.unity_catalog_volume)
    storage_account = _clean(config.azure_storage_account)
    container = _clean(config.azure_container)

    if unity_catalog_volume:
        if storage_account or container:
            raise ValueError(
                "Use either unity_catalog_volume or direct Azure ADLS ingestion paths, not both"
            )

        return IngestionPaths(
            source_path=build_volume_path(
                config.catalog,
                config.schema,
                unity_catalog_volume,
                config.volume_source_path,
            ),
            checkpoint_path=build_volume_path(
                config.catalog,
                config.schema,
                unity_catalog_volume,
                config.volume_checkpoint_path,
            ),
            schema_location=build_volume_path(
                config.catalog,
                config.schema,
                unity_catalog_volume,
                config.volume_schema_location,
            ),
        )

    if storage_account or container:
        if not storage_account or not container:
            raise ValueError("Both azure_storage_account and azure_container are required for Azure ingestion")

        return IngestionPaths(
            source_path=build_abfss_uri(container, storage_account, config.azure_source_path),
            checkpoint_path=build_abfss_uri(container, storage_account, config.azure_checkpoint_path),
            schema_location=build_abfss_uri(container, storage_account, config.azure_schema_location),
        )

    return IngestionPaths(
        source_path=_clean(config.source_path).rstrip("/"),
        checkpoint_path=_clean(config.checkpoint_path).rstrip("/"),
        schema_location=_clean(config.schema_location).rstrip("/"),
    )


def build_adls_oauth_conf(config: AzureIngestionConfig) -> Dict[str, str]:
    """Return Spark Hadoop conf for service-principal auth, or empty if unmanaged here."""
    storage_account = _clean(config.azure_storage_account)
    oauth_values = {
        "azure_tenant_id": _clean(config.azure_tenant_id),
        "azure_client_id": _clean(config.azure_client_id),
        "azure_client_secret": _clean(config.azure_client_secret),
    }

    if not any(oauth_values.values()):
        return {}

    missing = [name for name, value in oauth_values.items() if not value]
    if not storage_account:
        missing.insert(0, "azure_storage_account")
    if missing:
        raise ValueError(f"Incomplete Azure OAuth configuration: missing {', '.join(missing)}")

    account_host = f"{storage_account}.dfs.core.windows.net"
    return {
        f"fs.azure.account.auth.type.{account_host}": "OAuth",
        f"fs.azure.account.oauth.provider.type.{account_host}": (
            "org.apache.hadoop.fs.azurebfs.oauth2.ClientCredsTokenProvider"
        ),
        f"fs.azure.account.oauth2.client.id.{account_host}": oauth_values["azure_client_id"],
        f"fs.azure.account.oauth2.client.secret.{account_host}": oauth_values["azure_client_secret"],
        f"fs.azure.account.oauth2.client.endpoint.{account_host}": (
            f"https://login.microsoftonline.com/{oauth_values['azure_tenant_id']}/oauth2/token"
        ),
    }
