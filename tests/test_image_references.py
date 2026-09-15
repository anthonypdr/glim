import io
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from rich.console import Console
from glim.conversation import referenced_images
from glim.cli import Glim
from glim.agent import Agent


class ImageReferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'test image.png'
        self.path.write_bytes(b'\x89PNG\r\n\x1a\n')

    def test_leading_inline_and_spaced_references(self):
        for text in (f'@"{self.path}" What\'s here?', f'What\'s here? @"{self.path}"',
                     f'@ "{self.path}" What\'s here?'):
            with self.subTest(text=text):
                question, content = referenced_images(text)
                self.assertEqual(question, "What's here?")
                self.assertEqual(content[1]['type'], 'image_url')
                self.assertNotIn(str(self.path), str(content))

    def test_plain_agent_and_email_remain_text(self):
        for text in ('@ inspect the project', '@ run ls', 'Email a@b.png', 'hello'):
            self.assertEqual(referenced_images(text), (text, None))

    def test_missing_file_is_not_silently_sent_as_prose(self):
        with self.assertRaises(FileNotFoundError):
            referenced_images(f'@"{self.path.parent}/missing.png" Explain')

    def test_multiple_images_and_default_question(self):
        question, content = referenced_images(f'@"{self.path}" @"{self.path}"')
        self.assertEqual(question, 'Describe this image.')
        self.assertEqual(len(content), 3)

    def test_queue_routes_image_to_chat_and_agent_request_to_tools(self):
        app = Glim.__new__(Glim)
        app.pending = queue.Queue()
        image = f'@"{self.path}" Describe'
        agent = f'@ fix the layout using @"{self.path}"'
        for item in (image, agent, None):
            app.pending.put(item)
        app.session = Mock(); app.console = Mock(); app.title_attempted = True
        app.normal_chat = Mock(); app.handle_agent_request = Mock()
        app.process_requests()
        app.normal_chat.assert_called_once_with(image)
        app.handle_agent_request.assert_called_once_with(agent)

    def test_agent_receives_image_content_in_shared_history(self):
        history = []; lm = Mock()
        lm.chat.return_value = {'choices':[{'message':{'content':'Done'}}]}
        agent = Agent(lm, 'test', Console(file=io.StringIO()))
        agent.run(f'fix the layout using @"{self.path}"', history=history)
        parts = history[0]['content']
        self.assertEqual(parts[0]['text'], 'fix the layout using')
        self.assertEqual(parts[1]['type'], 'image_url')
        self.assertEqual(lm.chat.call_args.args[1][1]['content'], parts)

    def test_explicit_command_does_not_interpret_image_arguments(self):
        agent = Agent(Mock(), 'test', Console(file=io.StringIO()))
        with patch.object(agent, '_run_direct_command', return_value='ok') as run:
            agent.run('run echo @missing.png')
        run.assert_called_once_with('echo @missing.png')


if __name__ == '__main__':
    unittest.main()
