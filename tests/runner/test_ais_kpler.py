"""
tests/runner/test_ais_kpler.py

Coverage for runner.main's Kpler Maritime 2.0 GraphQL vessel fetch --
the successor to the discontinued MarineTraffic REST Vessels API.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from runner import main as runner_main


class TestNormVesselKpler(unittest.TestCase):

    def test_maps_nested_graphql_shape_to_flat_schema(self):
        node = {
            "staticData": {"name": "  SEA BREEZE  ", "mmsi": 123456789, "shipType": "Tanker"},
            "lastPositionUpdate": {
                "latitude": 38.9, "longitude": -76.8, "speed": 12.3,
                "course": 180.0, "heading": 179, "navigationalStatus": "Under way",
            },
        }
        out = runner_main._norm_vessel_kpler(node)
        self.assertEqual(out["mmsi"], "123456789")
        self.assertEqual(out["name"], "SEA BREEZE")
        self.assertEqual(out["lat"], 38.9)
        self.assertEqual(out["lon"], -76.8)
        self.assertEqual(out["sog"], 12.3)
        self.assertEqual(out["cog"], 180.0)
        self.assertEqual(out["nav_status"], "Under way")
        self.assertEqual(out["ship_type"], "Tanker")

    def test_handles_missing_nested_objects(self):
        out = runner_main._norm_vessel_kpler({})
        self.assertEqual(out["mmsi"], "")
        self.assertEqual(out["name"], "")
        self.assertIsNone(out["lat"])


class TestFetchKplerVessels(unittest.IsolatedAsyncioTestCase):

    @patch("runner.main.httpx.AsyncClient")
    async def test_success_returns_normalized_vessels(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "data": {
                "vessels": {
                    "nodes": [
                        {
                            "staticData": {"name": "ONE", "mmsi": 111, "shipType": "Cargo"},
                            "lastPositionUpdate": {"latitude": 1.0, "longitude": 2.0},
                        }
                    ]
                }
            }
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await runner_main._fetch_kpler_vessels(38.9, -76.8, 150)

        self.assertEqual(result["source"], "marinetraffic.com")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["vessels"][0]["mmsi"], "111")

        # Confirm the query actually targets the current Kpler endpoint, not
        # the discontinued MarineTraffic REST URL.
        call_args = mock_client.post.call_args
        self.assertEqual(call_args.args[0], "https://api.sml.kpler.com/graphql")
        self.assertIn("Bearer", call_args.kwargs["headers"]["Authorization"])

    @patch("runner.main.httpx.AsyncClient")
    async def test_graphql_error_raises(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "errors": [{"message": "Token is not valid", "extensions": {"code": "UNAUTHENTICATED"}}],
            "data": None,
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        with self.assertRaises(RuntimeError) as ctx:
            await runner_main._fetch_kpler_vessels(38.9, -76.8, 150)
        self.assertIn("Token is not valid", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
