"""REGISTRY 키와 크롤러 platform 필드 일치 회귀 테스트.

불일치하면 같은 글이 두 플랫폼명으로 중복 저장된다 (everyto/every.to 실사례).
"""

import unittest

from skim_core.crawlers import REGISTRY


class RegistryPlatformTests(unittest.TestCase):
    def test_registry_keys_match_crawler_platform(self):
        for name, crawler_cls in REGISTRY.items():
            self.assertEqual(name, crawler_cls.platform)


if __name__ == "__main__":
    unittest.main()
