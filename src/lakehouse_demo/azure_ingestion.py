"""Azure ADLS configuration helpers for Databricks Auto Loader ingestion."""

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


@dataclass(frozen=True)
class IngestionPaths:
    source_path: str
    checkpoint_path: str
    schema_location: str


@dataclass(frozen=True)
class AzureIngestionConfig:
    source_path: str = DEFAULT_SOURCE_PATH
    checkpoint_path: str = DEFAULT_CHECKPOINT_PATH
    schema_location: str = DEFAULT_SCHEMA_LOCATION
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


def resolve_ingestion_paths(config: AzureIngestionConfig) -> IngestionPaths:
    """Resolve DBFS defaults or Azure ADLS paths for the bronze ingestion task."""
    storage_account = _clean(config.azure_storage_account)
    container = _clean(config.azure_container)

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
