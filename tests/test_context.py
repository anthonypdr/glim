import json
import unittest
from unittest.mock import Mock, patch

from glim.lmstudio import LMStudio


class ContextUsageTests(unittest.TestCase):
    def test_uses_loaded_context_instead_of_maximum(self):
        models = LMStudio._lmstudio_models({"models": [{
            "type": "llm", "key": "gemma", "max_context_length": 262144,
            "loaded_instances": [{"config": {"context_length": 4096}}],
        }]})
        self.assertEqual(models[0]["context_length"], 4096)

    def test_usage_only_stream_event_updates_counter_without_breaking_content(self):
        lm = LMStudio()
        lm.has_loaded_state = True
        response = Mock()
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 28, "total_tokens": 128}},
        ]
        response.iter_lines.return_value = [
            ("data: " + json.dumps(chunk)).encode() for chunk in chunks
        ] + [b"data: [DONE]"]
        with patch("glim.lmstudio.requests.post", return_value=response) as post:
            received = list(lm.stream_chat("gemma", [{"role": "user", "content": "Hi"}]))
        self.assertEqual(received, chunks[:1])
        self.assertEqual(lm.context_usage, {"model": "gemma", "tokens": 128, "estimated": False})
        self.assertEqual(post.call_args.kwargs["json"]["stream_options"], {"include_usage": True})
        response.close.assert_called_once()

    def test_agent_usage_replaces_estimate_and_missing_usage_stays_estimated(self):
        lm = LMStudio()
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "Done"}}],
                                     "usage": {"total_tokens": 250}}
        with patch("glim.lmstudio.requests.post", return_value=response):
            lm.chat("gemma", [{"role": "user", "content": "Build the game"}], tools=[{"name": "read_file"}])
        self.assertEqual(lm.context_usage["tokens"], 250)
        self.assertFalse(lm.context_usage["estimated"])
        lm._begin_usage("other", [{"role": "user", "content": "A different request"}], None)
        self.assertTrue(lm.context_usage["estimated"])
        self.assertEqual(lm.context_usage["model"], "other")


if __name__ == "__main__":
    unittest.main()
