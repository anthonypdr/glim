import copy
import io
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from glim.agent import Agent
from glim.cli import Glim
from glim.conversation import chat_history


class ConversationTests(unittest.TestCase):
    def test_chat_agent_and_followups_share_history_and_tool_evidence(self):
        glim = Glim.__new__(Glim)
        glim.console = Console(file=io.StringIO())
        glim.model = "test-model"
        glim.messages = []
        glim.notify_done = Mock()
        glim.lm = Mock()
        glim.persistent_composer = True
        sent = []

        def stream(model, messages):
            sent.append(copy.deepcopy(messages))
            yield {"choices": [{"delta": {"content": "Use blue pipes."}}]}

        glim.lm.stream_chat.side_effect = stream
        glim.normal_chat("The game is flappy-bird. Make the pipes blue.")
        agent_calls = []
        replies = iter([
            {"role": "assistant", "content": "Checking the game.", "tool_calls": [
                {"id": "read-1", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"flappy-bird/game.js"}'}}
            ]},
            {"role": "assistant", "content": "The game uses blue pipes."},
            {"role": "assistant", "content": "I remember the game and its blue pipes."},
        ])

        def chat(model, messages, **kwargs):
            agent_calls.append(copy.deepcopy(messages))
            return {"choices": [{"message": next(replies)}]}

        glim.lm.chat.side_effect = chat
        with patch.dict("glim.agent.TOOL_FUNCTIONS", {
            "read_file": lambda **kwargs: {"ok": True, "content": "pipeColor = 'blue'", "diff": "preview only"}
        }):
            glim.handle_agent_request("@ inspect the game we discussed")
            glim.handle_agent_request("@ what color did we pick?")

        self.assertEqual(agent_calls[0][1]["content"], "The game is flappy-bird. Make the pipes blue.")
        self.assertEqual(agent_calls[0][2]["content"], "Use blue pipes.")
        observed = [m for m in agent_calls[-1] if m["role"] == "tool"]
        self.assertEqual(len(observed), 1)
        self.assertIn("pipeColor", observed[0]["content"])
        self.assertNotIn("preview only", observed[0]["content"])
        self.assertEqual(agent_calls[-1][-1]["content"], "what color did we pick?")

        glim.normal_chat("Explain what you found")
        plain = sent[-1]
        self.assertFalse(any(m["role"] in ("system", "tool") or "tool_calls" in m for m in plain))
        self.assertIn("pipeColor", str(plain))
        self.assertIn("flappy-bird/game.js", str(plain))
        self.assertIn("I remember the game", str(plain))
        self.assertFalse(any(m["role"] == "system" for m in glim.messages))

    def test_direct_command_and_failure_are_remembered(self):
        history = []
        lm = Mock()
        agent = Agent(lm, "test", Console(file=io.StringIO()))
        with patch.object(agent, "_run_direct_command", return_value="Created flappy-bird"):
            agent.run("run mkdir flappy-bird", history=history)
        self.assertEqual(history[-1]["content"], "Created flappy-bird")
        lm.chat.side_effect = RuntimeError("Disconnected")
        with self.assertRaises(RuntimeError):
            agent.run("inspect it", history=history)
        self.assertEqual(history[-2]["content"], "inspect it")
        self.assertIn("Disconnected", history[-1]["content"])

    def test_plain_projection_does_not_mutate_agent_history(self):
        messages = [
            {"role": "assistant", "content": "Looking", "tool_calls": [{"function": {"name": "list_files"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "flappy-bird"},
        ]
        original = copy.deepcopy(messages)
        projected = chat_history(messages)
        projected[0]["content"] = "changed"
        self.assertEqual(messages, original)


if __name__ == "__main__":
    unittest.main()
