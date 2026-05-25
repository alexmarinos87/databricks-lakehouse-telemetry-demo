import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.azure_ingestion import (  # noqa: E402
    AzureIngestionConfig,
    build_abfss_uri,
    build_adls_oauth_conf,
    resolve_ingestion_paths,
)


class AzureIngestionConfigTest(unittest.TestCase):
    def test_build_abfss_uri_normalizes_path_parts(self):
        uri = build_abfss_uri("landing", "demostore", "/lakehouse_demo/raw_machine_events/")

        self.assertEqual(
            "abfss://landing@demostore.dfs.core.windows.net/lakehouse_demo/raw_machine_events",
            uri,
        )

    def test_resolve_ingestion_paths_keeps_dbfs_defaults_without_azure_config(self):
        paths = resolve_ingestion_paths(AzureIngestionConfig())

        self.assertEqual("dbfs:/FileStore/lakehouse_demo/raw_machine_events", paths.source_path)
        self.assertEqual(
            "dbfs:/FileStore/lakehouse_demo/_checkpoints/bronze_machine_events",
            paths.checkpoint_path,
        )
        self.assertEqual(
            "dbfs:/FileStore/lakehouse_demo/_schemas/bronze_machine_events",
            paths.schema_location,
        )

    def test_resolve_ingestion_paths_uses_abfss_when_azure_is_configured(self):
        paths = resolve_ingestion_paths(
            AzureIngestionConfig(
                azure_storage_account="demostore",
                azure_container="landing",
                azure_source_path="machine-events/incremental",
            )
        )

        self.assertEqual(
            "abfss://landing@demostore.dfs.core.windows.net/machine-events/incremental",
            paths.source_path,
        )
        self.assertEqual(
            "abfss://landing@demostore.dfs.core.windows.net/lakehouse_demo/_checkpoints/bronze_machine_events",
            paths.checkpoint_path,
        )

    def test_resolve_ingestion_paths_requires_account_and_container_together(self):
        with self.assertRaisesRegex(ValueError, "azure_storage_account and azure_container"):
            resolve_ingestion_paths(AzureIngestionConfig(azure_storage_account="demostore"))

    def test_build_adls_oauth_conf_returns_spark_conf_for_service_principal(self):
        conf = build_adls_oauth_conf(
            AzureIngestionConfig(
                azure_storage_account="demostore",
                azure_tenant_id="tenant-123",
                azure_client_id="client-123",
                azure_client_secret="secret-value",
            )
        )

        host = "demostore.dfs.core.windows.net"
        self.assertEqual("OAuth", conf[f"fs.azure.account.auth.type.{host}"])
        self.assertEqual("client-123", conf[f"fs.azure.account.oauth2.client.id.{host}"])
        self.assertEqual("secret-value", conf[f"fs.azure.account.oauth2.client.secret.{host}"])
        self.assertEqual(
            "https://login.microsoftonline.com/tenant-123/oauth2/token",
            conf[f"fs.azure.account.oauth2.client.endpoint.{host}"],
        )

    def test_build_adls_oauth_conf_rejects_partial_service_principal_config(self):
        with self.assertRaisesRegex(ValueError, "azure_client_secret"):
            build_adls_oauth_conf(
                AzureIngestionConfig(
                    azure_storage_account="demostore",
                    azure_tenant_id="tenant-123",
                    azure_client_id="client-123",
                )
            )


if __name__ == "__main__":
    unittest.main()
