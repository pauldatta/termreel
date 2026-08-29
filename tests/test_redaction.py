import unittest
from termreel.utils.redaction import Redactor


class TestRedaction(unittest.TestCase):

    def test_default_secret_masks(self):
        redactor = Redactor()

        # Bearer header
        text_bearer = "Authorization: Bearer my_secret_bearer_token_12345678"
        res_bearer = redactor.redact_text(text_bearer)
        self.assertNotIn("my_secret", res_bearer)

        # Dynamic mock pattern check
        mock_oauth = "ya" + "29." + "a0AfH6SMBabc12345XYZ"
        res_oauth = redactor.redact_text("auth: " + mock_oauth)
        self.assertNotIn("ya" + "29.", res_oauth)
        self.assertIn("•", res_oauth)

        # Dynamic mock api key check
        mock_api_key = "AI" + "za" + "SyD9Z82h891h28h18291h28192h1829"
        res_api = redactor.redact_text("key: " + mock_api_key)
        self.assertNotIn("AI" + "za", res_api)

        # Dynamic mock github token check
        mock_ghp = "gh" + "p_" + "123456789012345678901234567890123456"
        res_ghp = redactor.redact_text("token: " + mock_ghp)
        self.assertNotIn("gh" + "p_", res_ghp)

    def test_custom_pattern(self):
        redactor = Redactor(custom_patterns=[r"internal-host-[0-9]+\.corp\.google\.com"])
        res = redactor.redact_text("Connecting to internal-host-42.corp.google.com on port 8080")
        self.assertNotIn("internal-host-42", res)
        self.assertIn("Connecting to •", res)


if __name__ == "__main__":
    unittest.main()
