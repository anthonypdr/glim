import asyncio
import base64
import json
import queue
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from glim.clipboard import ClipboardAttachments, _linux, _windows, _mac, _run
from glim.cli import Glim
from glim.conversation import referenced_images

PNG = b'\x89PNG\r\n\x1a\nfixture'


class ClipboardTests(unittest.TestCase):
    def test_wayland_prefers_image_over_text(self):
        with patch.dict('os.environ', {'WAYLAND_DISPLAY':'test'}), patch('glim.clipboard.shutil.which', return_value='/bin/wl-paste'), patch('glim.clipboard._run', side_effect=[b'text/plain\nimage/png\n', PNG]) as run:
            self.assertEqual(_linux(), ('image', PNG))
        self.assertEqual(run.call_args.args[0], ['wl-paste','--no-newline','--type','image/png'])

    def test_x11_and_text_fallback(self):
        with patch.dict('os.environ', {'DISPLAY':':99'}, clear=True), patch('glim.clipboard.shutil.which', return_value='/bin/xclip'), patch('glim.clipboard._run', side_effect=[b'UTF8_STRING\n', b"What's here?\n"]):
            self.assertEqual(_linux(), ('text', "What's here?\n"))

    def test_missing_backend_has_actionable_error(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'wl-clipboard'):
                _linux()

    def test_windows_returns_png_from_snipping_clipboard(self):
        payload = json.dumps({'kind':'image','data':base64.b64encode(PNG).decode()}).encode()
        with patch('glim.clipboard._run', return_value=payload) as run:
            self.assertEqual(_windows('powershell.exe'), ('image', PNG))
        self.assertIn('-STA', run.call_args.args[0])
        self.assertIn('ContainsImage', run.call_args.args[0][-1])

    def test_windows_unicode_text(self):
        value = 'Hello 世界\n'
        payload = json.dumps({'kind':'text','data':base64.b64encode(value.encode()).decode()}).encode()
        with patch('glim.clipboard._run', return_value=payload):
            self.assertEqual(_windows('powershell.exe'), ('text', value))

    def test_mac_text_fallback(self):
        with patch('glim.clipboard._run', side_effect=[ValueError('No image'), b'hello\n']):
            self.assertEqual(_mac(), ('text', 'hello'))

    def test_timeout_and_empty_clipboard_are_reported(self):
        import subprocess
        with patch('glim.clipboard.subprocess.run', side_effect=subprocess.TimeoutExpired('test', 10)):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                _run(['test'])
        with patch.dict('os.environ', {'WAYLAND_DISPLAY':'test'}), patch('glim.clipboard.shutil.which', return_value='wl-paste'), patch('glim.clipboard._run', return_value=b''):
            with self.assertRaisesRegex(ValueError, 'No image or text'):
                _linux()

    def test_attachment_marker_resolves_to_actual_image_and_cleans_up(self):
        store = ClipboardAttachments(); self.addCleanup(store.close)
        marker = store.add(PNG)
        self.assertEqual(marker, '@clipboard-1.png')
        self.assertNotIn('/tmp', marker)
        text = store.resolve('Explain ' + marker)
        question, parts = referenced_images(text)
        self.assertEqual(question, 'Explain')
        self.assertEqual(parts[1]['image_url']['url'], 'data:image/png;base64,' + base64.b64encode(PNG).decode())
        folder = Path(store.directory.name)
        self.assertEqual(store.resolve('No attachment'), 'No attachment')
        store.close()
        self.assertFalse(folder.exists())

    def test_invalid_and_oversized_clipboard_images_not_attached(self):
        store = ClipboardAttachments(); self.addCleanup(store.close)
        with self.assertRaisesRegex(ValueError, 'PNG'):
            store.add(b'text is not an image')
        with patch('glim.clipboard.MAX_BYTES', 4):
            with self.assertRaisesRegex(ValueError, '20 MB'):
                store.add(PNG)
        self.assertEqual(store.paths, {})

    def test_queued_marker_survives_until_worker_reads_image(self):
        app = Glim.__new__(Glim); app.pending = queue.Queue()
        app.clipboard_attachments = ClipboardAttachments()
        self.addCleanup(app.clipboard_attachments.close)
        app.pending.put(app.clipboard_attachments.add(PNG) + ' Explain')
        app.pending.put(None)
        app.session = Mock(); app.console = Mock(); app.title_attempted = True
        observed = []
        app.normal_chat = lambda text: observed.append(referenced_images(text)[1])
        app.handle_agent_request = Mock()
        app.process_requests()
        self.assertEqual(observed[0][1]['type'], 'image_url')
        app.handle_agent_request.assert_not_called()
        self.assertEqual(app.clipboard_attachments.paths, {})


class PasteKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_v_and_alt_v_attach_without_submitting(self):
        for key in ('\x16', '\x1bv'):
            with self.subTest(key=key), create_pipe_input() as pipe, patch('glim.cli.create_output', return_value=DummyOutput()), create_app_session(input=pipe), patch('glim.cli.read_clipboard', return_value=('image', PNG)):
                app = Glim(); app.session.app.input = pipe
                task = asyncio.create_task(app.session.prompt_async('> '))
                try:
                    await asyncio.sleep(.03)
                    pipe.send_text(key)
                    for _ in range(100):
                        await asyncio.sleep(.01)
                        if '@clipboard-1.png' in app.session.default_buffer.text:
                            break
                    self.assertIn('@clipboard-1.png', app.session.default_buffer.text)
                    self.assertFalse(task.done())
                    self.assertIn('attached', app.clipboard_status)
                    pipe.send_text('What is shown?\r')
                    result = await asyncio.wait_for(task, 2)
                    self.assertIn('What is shown?', result)
                    _, parts = referenced_images(app.clipboard_attachments.resolve(result))
                    self.assertEqual(parts[1]['type'], 'image_url')
                finally:
                    if not task.done():
                        app.session.app.exit()
                        await task
                    app.clipboard_attachments.close()

    async def test_pasted_text_with_newlines_never_auto_submits(self):
        with create_pipe_input() as pipe, patch('glim.cli.create_output', return_value=DummyOutput()), create_app_session(input=pipe), patch('glim.cli.read_clipboard', return_value=('text', 'hello\r\nworld')):
            app = Glim(); app.session.app.input = pipe
            task = asyncio.create_task(app.session.prompt_async('> '))
            try:
                await asyncio.sleep(.03); pipe.send_text('\x16')
                for _ in range(100):
                    await asyncio.sleep(.01)
                    if app.session.default_buffer.text:
                        break
                self.assertEqual(app.session.default_buffer.text, 'hello\nworld')
                self.assertFalse(task.done())
            finally:
                app.session.app.exit(); await task
                app.clipboard_attachments.close()

    async def test_clipboard_error_keeps_the_draft(self):
        app = Glim.__new__(Glim); app.clipboard_busy = False; app.session = Mock()
        buffer = Mock()
        with patch('glim.cli.asyncio.to_thread', new=AsyncMock(side_effect=ValueError('No image or text'))):
            await app.paste_clipboard(buffer)
        buffer.insert_text.assert_not_called()
        self.assertFalse(app.clipboard_busy)
        self.assertIn('No image', app.clipboard_status)

if __name__ == '__main__':
    unittest.main()
