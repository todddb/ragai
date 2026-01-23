import unittest

from app.workers.crawl_worker import resolve_fetch_mode


class CrawlFetchModeTests(unittest.TestCase):
    def test_resolves_http_when_no_auth_profile(self) -> None:
        rule = {"pattern": "https://example.com/", "auth_profile": None}
        self.assertEqual(resolve_fetch_mode(rule), "http")

    def test_resolves_playwright_when_auth_profile_set(self) -> None:
        rule = {"pattern": "https://policy.byu.edu/", "auth_profile": "policy_cas"}
        self.assertEqual(resolve_fetch_mode(rule), "playwright")

    def test_resolves_http_when_rule_missing(self) -> None:
        self.assertEqual(resolve_fetch_mode(None), "http")


if __name__ == "__main__":
    unittest.main()
