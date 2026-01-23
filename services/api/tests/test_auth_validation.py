import unittest

from app.utils.auth_validation import detect_auth_failure, resolve_test_url


class AuthValidationTests(unittest.TestCase):
    def test_detects_idp_redirect(self) -> None:
        """Test that redirect to IdP domain is detected"""
        reason = detect_auth_failure(
            "https://cas.byu.edu/cas/login",
            "Central Authentication Service",
            "<html></html>",
        )
        self.assertIsNotNone(reason)
        self.assertIn("cas.byu.edu", reason)

    def test_detects_cas_login_path(self) -> None:
        """Test that CAS login path is detected even without IdP domain"""
        reason = detect_auth_failure(
            "https://someserver.edu/cas/login?service=...",
            "Login Page",
            "<html></html>",
        )
        self.assertIsNotNone(reason)
        self.assertEqual(reason, "cas_login_path_detected")

    def test_detects_cas_title(self) -> None:
        """Test that CAS title marker is detected"""
        reason = detect_auth_failure(
            "https://policy.byu.edu/",
            "Central Authentication Service",
            "<html><body>Please log in</body></html>",
        )
        self.assertIsNotNone(reason)
        self.assertEqual(reason, "title_indicates_login")

    def test_no_false_positive_for_footer_login_text(self) -> None:
        """Test that normal page with 'Log in' text in footer does NOT trigger auth failure"""
        html_with_footer_login = """
        <html>
        <head><title>Business Gifts Policy</title></head>
        <body>
            <h1>Policy Information</h1>
            <p>This is a normal policy page with lots of content.</p>
            <footer>
                <a href="/login">Log in</a> to access more features.
            </footer>
        </body>
        </html>
        """
        reason = detect_auth_failure(
            "https://policy.byu.edu/view/business-gifts",
            "Business Gifts Policy",
            html_with_footer_login,
        )
        # Should NOT detect auth failure for a normal page
        self.assertIsNone(reason)

    def test_detects_cas_login_form(self) -> None:
        """Test that CAS login form with multiple markers IS detected"""
        cas_login_html = """
        <html>
        <head><title>CAS Login</title></head>
        <body>
            <form action="/cas/login" method="post">
                <input type="text" name="username" id="username" />
                <input type="password" name="password" id="password" />
                <button type="submit">Login</button>
            </form>
        </body>
        </html>
        """
        reason = detect_auth_failure(
            "https://someserver.edu/login",
            "Login Page",
            cas_login_html,
        )
        # Should detect auth failure due to multiple CAS markers
        self.assertIsNotNone(reason)
        self.assertEqual(reason, "cas_login_form_detected")

    def test_no_false_positive_single_marker(self) -> None:
        """Test that a single generic marker alone doesn't trigger false positive"""
        html_with_single_marker = """
        <html>
        <head><title>Help Documentation</title></head>
        <body>
            <p>If you need help, please <a href="/contact">log in</a> to the support portal.</p>
        </body>
        </html>
        """
        reason = detect_auth_failure(
            "https://help.example.edu/docs",
            "Help Documentation",
            html_with_single_marker,
        )
        # Should NOT detect auth failure - only one generic marker, not enough confidence
        self.assertIsNone(reason)

    def test_resolves_test_url_from_seed(self) -> None:
        """Test that test URL is properly resolved from allow rules"""
        profile = {}
        allow_block = {
            "allow_rules": [
                {"pattern": "https://policy.byu.edu/view/", "auth_profile": "policy_cas"}
            ]
        }
        resolved = resolve_test_url(profile, allow_block, "policy_cas")
        self.assertEqual(resolved, "https://policy.byu.edu/view/")

    def test_resolves_test_url_from_profile_test_url(self) -> None:
        """Test that explicit test_url takes precedence"""
        profile = {"test_url": "https://policy.byu.edu/protected"}
        allow_block = {"allow_rules": []}
        resolved = resolve_test_url(profile, allow_block, "policy_cas")
        self.assertEqual(resolved, "https://policy.byu.edu/protected")

    def test_resolves_test_url_from_start_url(self) -> None:
        """Test that start_url is used when no allow rule matches"""
        profile = {"start_url": "https://policy.byu.edu/"}
        allow_block = {"allow_rules": []}
        resolved = resolve_test_url(profile, allow_block, "policy_cas")
        self.assertEqual(resolved, "https://policy.byu.edu/")


if __name__ == "__main__":
    unittest.main()
