import tempfile
import unittest
from pathlib import Path

from app.utils.config import _load_yaml, write_yaml_config


class ConfigIOTests(unittest.TestCase):
    def test_write_and_load_yaml_config(self) -> None:
        payload = {
            "seed_urls": [{"url": "https://example.com/", "allow_http": False}],
            "blocked_domains": ["cas.example.com"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "allow_block.yml"
            write_yaml_config(path, payload)
            loaded = _load_yaml(path)
        self.assertEqual(loaded, payload)


if __name__ == "__main__":
    unittest.main()
