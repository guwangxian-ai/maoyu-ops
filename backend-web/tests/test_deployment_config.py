"""Keep the shared-server Compose configuration compatible with app settings."""
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.core.config import BaseConfig


class DeploymentConfigTests(unittest.TestCase):
    def test_shared_server_database_pool_is_accepted_by_settings(self):
        compose = yaml.safe_load((ROOT / "deploy/tencent-hermes/compose.yaml").read_text())
        for service in ("backend-web", "websocket", "scheduler"):
            with self.subTest(service=service):
                environment = compose["services"][service]["environment"]
                size = int(environment["DB_POOL_SIZE"])
                overflow = int(environment["DB_MAX_OVERFLOW"])
                settings = BaseConfig(_env_file=None, db_pool_size=size, db_max_overflow=overflow)
                self.assertEqual(settings.db_pool_size, size)
                self.assertEqual(settings.db_max_overflow, overflow)


if __name__ == "__main__":
    unittest.main()
