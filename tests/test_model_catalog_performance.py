from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vesta import app_state
from vestahub import provider_balance, provider_blocks, provider_reliability


class ModelCatalogPerformanceTests(unittest.TestCase):
    def test_catalog_loads_each_provider_health_store_once(self) -> None:
        models = [
            {
                "id": f"free:{provider}:{index}",
                "label": f"{provider}-{index}",
                "provider": provider,
                "kind": "free",
                "group": "free",
                "paid": False,
                "available": True,
            }
            for index, provider in enumerate(("alpha", "alpha", "beta"))
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch("vestahub.accounts.list_connected_accounts", return_value=[]),
                mock.patch(
                    "vestahub.accounts.provider_connection_doctor", return_value=[]
                ),
                mock.patch("vestahub.accounts.account_models", return_value=[]),
                mock.patch(
                    "vestahub.accounts.provider_contract_payload", return_value={}
                ),
                mock.patch("vestahub.free_models.list_free_models", return_value=models),
                mock.patch(
                    "vestahub.paid_api_models.list_paid_api_models", return_value=[]
                ),
                mock.patch("vestahub.local_runner.cached_local_models", return_value=[]),
                mock.patch.object(
                    provider_balance, "_load", wraps=provider_balance._load
                ) as balance_load,
                mock.patch.object(
                    provider_blocks, "_load", wraps=provider_blocks._load
                ) as blocks_load,
                mock.patch.object(
                    provider_reliability,
                    "_load",
                    wraps=provider_reliability._load,
                ) as reliability_load,
            ):
                catalog = app_state.available_models(root, discover_local=False)

            self.assertEqual(len(catalog["models"]), 4)
            self.assertEqual(balance_load.call_count, 1)
            self.assertEqual(blocks_load.call_count, 1)
            self.assertEqual(reliability_load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
