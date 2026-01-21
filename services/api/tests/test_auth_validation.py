import unittest

from app.utils.auth_validation import detect_auth_failure, resolve_test_url


class AuthValidationTests(unittest.TestCase):
    def test_detects_idp_redirect(self) -> None:
        reason = detect_auth_failure(
            "https://cas.byu.edu/cas/login",
            "Central Authentication Service",
            "<html></html>",
        )
        self.assertIsNotNone(reason)
        self.assertIn("cas.byu.edu", reason)

    def test_resolves_test_url_from_seed(self) -> None:
        profile = {"use_for_domains": ["policy.byu.edu"]}
        allow_block = {"seed_urls": [{"url": "https://policy.byu.edu/view/"}]}
        resolved = resolve_test_url(profile, allow_block, "policy_cas")
        self.assertEqual(resolved, "https://policy.byu.edu/view/")


if __name__ == "__main__":
    unittest.main()
