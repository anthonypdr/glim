import unittest
from unittest.mock import Mock, patch

import requests

from glim.lmstudio import LMStudio


class CompatibleServerTests(unittest.TestCase):
    def test_root_and_v1_base_urls_reach_the_same_endpoints(self):
        for suffix in ("", "/", "/v1", "/v1/"):
            with self.subTest(suffix=suffix):
                lm = LMStudio("http://127.0.0.1:11434" + suffix)
                response = Mock()
                response.json.return_value = {"data": [{"id": "local-model"}]}
                with patch("glim.lmstudio.requests.get", side_effect=[
                    requests.HTTPError("Native endpoint unavailable"), response,
                ]) as get:
                    models = lm.get_loaded_models()
                self.assertEqual(models[0]["id"], "local-model")
                self.assertFalse(lm.has_loaded_state)
                self.assertEqual(get.call_args_list[0].args[0],
                                 "http://127.0.0.1:11434/api/v1/models")
                self.assertEqual(get.call_args_list[1].args[0],
                                 "http://127.0.0.1:11434/v1/models")
                with patch("glim.lmstudio.requests.post", return_value=response) as post:
                    lm.chat("local-model", [{"role": "user", "content": "Hello"}])
                self.assertEqual(post.call_args.args[0],
                                 "http://127.0.0.1:11434/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
