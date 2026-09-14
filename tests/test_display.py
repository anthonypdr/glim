import asyncio
import io
import unittest
import threading
from unittest.mock import Mock, patch

from prompt_toolkit.data_structures import Size
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from glim.agent import Agent
from glim.cli import Glim
from glim.display import StreamingMarkdown


class TerminalOutput(DummyOutput):
    def __init__(self, rows):
        self.rows = rows

    def get_size(self):
        return Size(rows=self.rows, columns=80)

    def get_rows_below_cursor_position(self):
        return self.rows


class PromptDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_tracks_content_in_short_and_tall_terminals(self):
        for rows in (24, 100):
            with create_pipe_input() as pipe, patch(
                "glim.cli.create_output", return_value=TerminalOutput(rows)
            ), create_app_session(input=pipe):
                glim = Glim()
                glim.session.app.input = pipe
                task = asyncio.create_task(glim.session.prompt_async("  > "))
                try:
                    await asyncio.sleep(0.05)
                    for text, expected_rows in (("", 3), ("x" * 160, 5), ("short", 3)):
                        glim.session.default_buffer.text = text
                        glim.session.app.invalidate()
                        await asyncio.sleep(0.05)
                        screen = glim.session.app.renderer._last_screen
                        shaded = [
                            y for y, line in screen.data_buffer.items()
                            if any("class:input-bar" in cell.style for cell in line.values())
                        ]
                        self.assertEqual(shaded, list(range(1, expected_rows + 1)))
                        footer = "".join(
                            cell.char for _, cell in sorted(screen.data_buffer[expected_rows + 1].items())
                        )
                        self.assertIn("No model", footer)
                    pipe.send_text("\n")
                    self.assertEqual(await task, "short")
                finally:
                    if not task.done():
                        glim.session.app.exit()
                        await task

    async def test_working_status_stays_above_input_with_long_metadata(self):
        with create_pipe_input() as pipe, patch(
            "glim.cli.create_output", return_value=TerminalOutput(24)
        ), create_app_session(input=pipe):
            glim = Glim()
            glim.model = "google/gemma-4-e4b"
            glim.conversation_name = "A long conversation title " * 4
            glim.session.app.input = pipe
            task = asyncio.create_task(glim.session.prompt_async("Ask anything… "))
            try:
                for working, approval, expected in (
                    (True, None, "Working…"),
                    (True, {"pending": True}, "Approval needed"),
                    (True, None, "Working…"),
                    (False, None, "Ready"),
                ):
                    glim.working = working
                    glim.approval = approval
                    # The periodic refresh must update status without typing.
                    await asyncio.sleep(0.2)
                    screen = glim.session.app.renderer._last_screen
                    status = "".join(cell.char for _, cell in sorted(screen.data_buffer[0].items()))
                    self.assertIn(expected, status)
                    self.assertTrue(any("class:input-bar" in cell.style
                                        for cell in screen.data_buffer[2].values()))
                pipe.send_text("\n")
                await task
            finally:
                if not task.done():
                    glim.session.app.exit()
                    await task

    async def test_composer_accepts_typing_while_agent_waits_for_approval(self):
        with create_pipe_input() as pipe, patch(
            "glim.cli.create_output", return_value=TerminalOutput(24)
        ), create_app_session(input=pipe):
            glim = Glim()
            glim.console = Console(file=io.StringIO())
            glim.persistent_composer = True
            glim.session.app.input = pipe
            prompt = asyncio.create_task(glim.session.prompt_async("  > "))
            result = []
            approval = threading.Thread(target=lambda: result.append(glim.confirm_command("npm test")))
            approval.start()
            try:
                for _ in range(50):
                    if glim.approval:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNotNone(glim.approval)
                pipe.send_text("follow-up draft")
                await asyncio.sleep(0.05)
                self.assertEqual(glim.session.default_buffer.text, "follow-up draft")
                self.assertTrue(approval.is_alive())
                glim.answer_approval(False)
                approval.join(timeout=1)
                self.assertEqual(result, [False])
                pipe.send_text("\n")
                self.assertEqual(await prompt, "follow-up draft")
            finally:
                if glim.approval:
                    glim.answer_approval(False)
                approval.join(timeout=1)
                if not prompt.done():
                    glim.session.app.exit()
                    await prompt


class AgentDisplayTests(unittest.TestCase):
    def test_worker_serializes_chat_and_agent_requests_after_failure(self):
        with patch("glim.cli.create_output", return_value=TerminalOutput(24)):
            glim = Glim()
        glim.console = Console(file=io.StringIO())
        calls = []

        def chat(text):
            calls.append(text)
            if text == "first":
                raise RuntimeError("Disconnected")

        glim.normal_chat = chat
        glim.handle_agent_request = calls.append
        for text in ("first", "@ second", "third", None):
            glim.pending.put(text)
        worker = threading.Thread(target=glim.process_requests)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, ["first", "@ second", "third"])
        self.assertEqual(glim.pending.unfinished_tasks, 0)
        self.assertFalse(glim.working)

    def test_background_stream_preserves_partial_code_on_failure(self):
        glim = Glim.__new__(Glim)
        stream = io.StringIO()
        glim.console = Console(file=stream)
        glim.model = "test-model"
        glim.messages = []
        glim.persistent_composer = True
        glim.lm = Mock()

        def broken_stream(*args):
            yield {"choices": [{"delta": {"content": "```python\nprint(42)"}}]}
            raise RuntimeError("Connection lost")

        glim.lm.stream_chat.side_effect = broken_stream
        glim.normal_chat("hello")
        self.assertIn("print(42)", stream.getvalue())
        self.assertIn("Connection lost", stream.getvalue())
        self.assertEqual(glim.messages[-1]["content"], "```python\nprint(42)")

    def test_streamed_fences_keep_blank_lines_and_have_highlighted_frame(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=True, no_color=False, color_system="truecolor", width=80)
        renderer = StreamingMarkdown(console)
        for part in ["Example:\n\n`", "``python\n", "def hello():\n\n", "    return 42\n", "```\n"]:
            renderer.feed(part)
        renderer.finish()
        output = stream.getvalue()
        self.assertIn("python", output)
        self.assertNotIn("╭", output)
        self.assertIn("38;2;", output)
        self.assertEqual(output.count("hello"), 1)

    def test_chat_renders_split_markdown_and_keeps_full_answer(self):
        glim = Glim.__new__(Glim)
        stream = io.StringIO()
        glim.console = Console(file=stream, width=80, height=10)
        glim.model = "test-model"
        glim.messages = []
        glim.notify_done = Mock()
        glim.lm = Mock()
        parts = ["**Bo", "ld**\n\n```python\n", "print('hello')\n```\n\n"]
        parts.extend(f"Item {i}\n\n" for i in range(30))
        glim.lm.stream_chat.return_value = iter(
            {"choices": [{"delta": {"content": part}}]} for part in parts
        )
        glim.normal_chat("hello")
        rendered = stream.getvalue()
        self.assertIn("Bold", rendered)
        self.assertNotIn("**Bold**", rendered)
        self.assertNotIn("```", rendered)
        self.assertIn("print('hello')", rendered)
        lines = [line.rstrip() for line in rendered.splitlines()]
        for i in range(30):
            self.assertEqual(lines.count(f"Item {i}"), 1)
        self.assertEqual(glim.messages[-1]["content"], "".join(parts))

    def test_chat_keeps_formatted_partial_answer_on_stream_failure(self):
        glim = Glim.__new__(Glim)
        stream = io.StringIO()
        glim.console = Console(file=stream)
        glim.model = "test-model"
        glim.messages = []
        glim.lm = Mock()

        def broken_stream(*args):
            yield {"choices": [{"delta": {"content": "**Partial answer**"}}]}
            raise RuntimeError("Connection lost")

        glim.lm.stream_chat.side_effect = broken_stream
        glim.normal_chat("hello")
        self.assertIn("Partial answer", stream.getvalue())
        self.assertNotIn("**Partial answer**", stream.getvalue())
        self.assertIn("Connection lost", stream.getvalue())

    def test_commentary_and_diff_survive_tool_turn(self):
        stream = io.StringIO()
        console = Console(file=stream, width=80)
        lm = Mock()
        lm.chat.side_effect = [
            {"choices": [{"message": {
                "role": "assistant", "content": "Updating the greeting.",
                "tool_calls": [{"id": "edit", "function": {
                    "name": "write_file", "arguments": '{"path": "hello.txt", "content": "hello"}'
                }}],
            }}]},
            {"choices": [{"message": {"content": "Finished."}}]},
        ]
        agent = Agent(lm, "test-model", console)

        def edit(**kwargs):
            self.assertFalse(console._live_stack)
            return {"ok": True, "path": "hello.txt", "diff": "-old\n+hello\n"}

        with patch.dict("glim.agent.TOOL_FUNCTIONS", {"write_file": edit}):
            self.assertEqual(agent.run("update greeting"), "Finished.")
        output = stream.getvalue()
        for expected in ("Updating the greeting.", "hello.txt", "-old", "+hello"):
            self.assertIn(expected, output)

    def test_sent_prompt_has_inner_vertical_padding(self):
        glim = Glim.__new__(Glim)
        stream = io.StringIO()
        glim.console = Console(file=stream, width=30)
        glim.print_user_message("hello")
        lines = stream.getvalue().splitlines()
        self.assertEqual(lines[1], " " * 30)
        self.assertTrue(lines[2].startswith("  > hello"))
        self.assertEqual(lines[3], " " * 30)


if __name__ == "__main__":
    unittest.main()
