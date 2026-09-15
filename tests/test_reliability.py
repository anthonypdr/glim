import copy
import io
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from rich.console import Console
from glim.cli import Glim
from glim.lmstudio import LMStudio
from glim.conversation import fit_context, image_prompt, estimate_tokens, chat_history, short_title


class ReliabilityTests(unittest.TestCase):
    def test_context_keeps_current_tool_pairs_and_system_without_mutation(self):
        messages = [
            {'role': 'system', 'content': 'instructions'},
            {'role': 'user', 'content': 'old'*1000},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'current'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'a'}]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'result'},
        ]
        before = copy.deepcopy(messages)
        kept, removed = fit_context(messages, 1000)
        self.assertEqual(removed, 2)
        self.assertEqual(kept, [messages[0], *messages[3:]])
        self.assertEqual(messages, before)

    def test_oversized_current_turn_does_not_send_invalid_request(self):
        lm = LMStudio(); lm.models = [{'id': 'test', 'context_length': 100}]
        with patch('glim.lmstudio.requests.post') as post:
            with self.assertRaisesRegex(ValueError, 'current turn is too large'):
                lm.chat('test', [{'role': 'user', 'content': 'x'*1000}])
        post.assert_not_called()

    def stream(self, events):
        lm = LMStudio(); response = Mock()
        response.iter_lines.return_value = events
        with patch('glim.lmstudio.requests.post', return_value=response):
            try:
                result = list(lm.stream_chat('test', [{'role': 'user', 'content': 'hello'}]))
            finally:
                response.close.assert_called_once()
        return lm, result

    def test_stream_errors_and_truncated_transport_are_visible(self):
        for events, error in [([b'data:{"error":"context overflow"}'], 'context overflow'),
                              ([b'data: {broken'], 'invalid stream'),
                              ([b'data: {"choices":[{"delta":{"content":"partial"}}]}'], 'disconnected')]:
            with self.subTest(events=events), self.assertRaisesRegex(RuntimeError, error):
                self.stream(events)

    def test_finish_reason_length_and_sse_without_space(self):
        lm, chunks = self.stream([b'data:{"choices":[{"delta":{},"finish_reason":"length"}]}', b'data:[DONE]'])
        self.assertEqual(lm.last_finish_reason, 'length')
        self.assertEqual(len(chunks), 1)

    def test_image_path_spaces_and_history_preserve_multimodal_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'my image.png'; path.write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*10000)
            prompt, content = image_prompt(f'/image "{path}" Explain this chart')
        self.assertEqual(prompt, 'Explain this chart')
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
        messages = [{'role':'user','content':content}]
        self.assertEqual(chat_history(messages), messages)
        self.assertLess(estimate_tokens(messages), 2200)

    def test_text_only_model_rejects_image_with_useful_message(self):
        lm = LMStudio(); lm.models = [{'id':'test', 'vision':False}]
        with self.assertRaisesRegex(ValueError, 'vision model'):
            lm.chat('test',[{'role':'user','content':[{'type':'image_url'}]}])

    def test_failed_request_does_not_poison_next_turn_and_partial_is_retained(self):
        app = Glim.__new__(Glim); app.model = 'test'; app.messages = []
        app.console = Console(file=io.StringIO()); app.lm = Mock(); app.notify_done = Mock()
        def failed(*args):
            raise RuntimeError('context overflow')
            yield
        app.lm.stream_chat.side_effect = failed
        app.normal_chat('failed question')
        self.assertEqual(app.messages, [])
        def partial(*args):
            yield {'choices':[{'delta':{'content':'partial answer'}}]}
            raise RuntimeError('disconnected')
        app.lm.stream_chat.side_effect = partial
        app.normal_chat('second question')
        self.assertEqual(app.messages[-1]['content'], 'partial answer')
        self.assertEqual(app.messages[0]['content'], 'second question')
        self.assertIn('disconnected', app.console.file.getvalue())

    def test_title_separate_from_history_usage_and_word_limit(self):
        lm = LMStudio(); lm.context_usage = {'tokens': 120}; response = Mock()
        response.json.return_value = {'choices':[{'message':{'content':'Local AI Chat Reliability'}}]}
        with patch('glim.lmstudio.requests.post',return_value=response) as post:
            self.assertEqual(lm.generate_title('test','fix stopping and image input'), 'Local AI Chat Reliability')
        self.assertEqual(lm.context_usage, {'tokens':120})
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'], 96)
        self.assertLessEqual(len(short_title('one two three four five six seven eight').split()),6)
        self.assertLessEqual(len(short_title('lengthy '*20)),48)

class WorkflowTests(unittest.TestCase):
    def test_queue_continues_after_failure_and_names_successful_turn(self):
        app = Glim.__new__(Glim)
        app.pending = queue.Queue()
        for item in ('first', 'second', None):
            app.pending.put(item)
        app.session = Mock(); app.console = Mock(); app.lm = Mock()
        app.model = 'test'; app.messages = []; app.title_attempted = False
        app.conversation_name = 'New conversation'
        app.lm.generate_title.return_value = 'Local Chat Improvements'
        def run(text):
            if text == 'first':
                raise RuntimeError('connection failed')
            app.messages.append({'role':'user', 'content':text})
        app.normal_chat = Mock(side_effect=run)
        app.process_requests()
        self.assertEqual(app.normal_chat.call_count, 2)
        self.assertEqual(app.pending.unfinished_tasks, 0)
        self.assertFalse(app.working)
        self.assertEqual(app.conversation_name, 'Local Chat Improvements')
        app.lm.generate_title.assert_called_once_with('test', 'second')

    def test_native_title_disables_reasoning_without_changing_chat_settings(self):
        lm = LMStudio(); lm.has_loaded_state = True
        lm.models = [{'id':'test', 'reasoning_options':['off', 'on']}]
        response = Mock(); response.json.return_value = {
            'output':[{'type':'reasoning','content':'hidden'},
                      {'type':'message','content':'Local Chat Improvements'}]}
        with patch('glim.lmstudio.requests.post',return_value=response) as post:
            self.assertEqual(lm.generate_title('test','request'), 'Local Chat Improvements')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['reasoning'], 'off')
        self.assertFalse(payload['store'])
        self.assertTrue(post.call_args.args[0].endswith('/api/v1/chat'))

    def test_image_question_preserves_apostrophe(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'my image.png'; path.write_bytes(b'\x89PNG\r\n\x1a\n')
            question, _ = image_prompt(f'/image "{path}" What\'s wrong here?')
        self.assertEqual(question, "What's wrong here?")


if __name__ == '__main__':
    unittest.main()
