from __future__ import annotations

import unittest
from unittest.mock import patch

from dops.auth import _metadata_candidates, _oauth_endpoint, discover_oauth


class OAuthDiscoveryTests(unittest.TestCase):
    def test_metadata_candidates_include_rfc8414_path_insertion(self) -> None:
        candidates = _metadata_candidates("https://auth.aidecisionops.com/oauth")
        self.assertIn("https://auth.aidecisionops.com/.well-known/oauth-authorization-server/oauth", candidates)
        self.assertIn("https://auth.aidecisionops.com/.well-known/openid-configuration/oauth", candidates)

    def test_oauth_endpoint_does_not_duplicate_oauth_segment(self) -> None:
        self.assertEqual(
            _oauth_endpoint("https://auth.aidecisionops.com/oauth", "authorize"),
            "https://auth.aidecisionops.com/oauth/authorize",
        )

    def test_discovery_fallback_uses_single_oauth_segment(self) -> None:
        with patch("dops.auth._get_json", side_effect=RuntimeError("forbidden")):
            discovery = discover_oauth({"issuerUrl": "https://auth.aidecisionops.com/oauth"})
        self.assertEqual(discovery.authorizationEndpoint, "https://auth.aidecisionops.com/oauth/authorize")
        self.assertEqual(discovery.tokenEndpoint, "https://auth.aidecisionops.com/oauth/token")
