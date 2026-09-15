"""Present agent observations to plain chat without enabling tool calling."""

import json
import base64
import re
import shlex
from pathlib import Path


def estimate_tokens(messages, tools=None):
    """A labelled fallback estimate, not a substitute for the model tokenizer."""
    if not messages and not tools:
        return 0
    # Image bytes are not text tokens. Reserve an approximate visual budget.
    images = 0
    def clean(value):
        nonlocal images
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                images += 1
                return {"type": "image_url"}
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    text = json.dumps(clean({"messages": messages, "tools": tools}), ensure_ascii=False)
    return max(1, (len(text) + 3) // 4) + images * 2048


def chat_history(messages):
    history = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            observation = "Tool result (observed output):\n" + message.get("content", "")
            if history and history[-1]["role"] == "assistant":
                history[-1]["content"] += "\n\n" + observation
            else:
                history.append({"role": "assistant", "content": observation})
        elif message.get("tool_calls"):
            content = message.get("content") or ""
            for call in message["tool_calls"]:
                function = call.get("function", {})
                content += "\n\nPrevious tool call:\n" + json.dumps(function, ensure_ascii=False)
            history.append({"role": "assistant", "content": content.strip()})
        else:
            history.append({"role": role, "content": message.get("content") or ""})
    return history


def fit_context(messages, limit, tools=None):
    """Drop whole old user turns; keep system instructions and current tool pairs.

    Leave half the loaded window for output and tokenizer estimation error.
    The source history is never modified.
    """
    kept = list(messages)
    if not isinstance(limit, int) or limit <= 0:
        return kept, 0
    budget = limit // 2
    removed = 0
    while estimate_tokens(kept, tools) > budget:
        starts = [i for i, message in enumerate(kept) if message['role'] == 'user']
        if len(starts) < 2:
            raise ValueError(
                "The current turn is too large for the loaded context window. "
                "Shorten the request, use /clear, or load the model with a larger context length."
            )
        begin, end = starts[:2]
        removed += end - begin
        del kept[begin:end]
    return kept, removed


def image_prompt(command):
    """Read /image PATH [question], accepting shell-quoted paths with spaces."""
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    lexer.get_token()  # /image
    filename = lexer.get_token()
    if not filename:
        raise ValueError('Usage: /image "/path/to/image.png" What is in this image?')
    path = Path(filename).expanduser()
    with path.open('rb') as source:
        data = source.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('Image must be 20 MB or smaller.')
    mime = image_mime(data)
    question = lexer.instream.read().strip() or 'Describe this image.'
    return question, [
        {'type': 'text', 'text': question},
        {'type': 'image_url', 'image_url': {
            'url': f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')}},
    ]


def short_title(value):
    value = re.sub(r'<think>.*?</think>', '', value, flags=re.S).strip()
    value = re.sub(r'^(?:title|chat title)\s*:\s*', '', value, flags=re.I)
    value = ' '.join(value.strip('"\'`# \n').split())
    words = value.split()[:6]
    while words and len(' '.join(words)) > 48:
        words.pop()
    return ' '.join(words).rstrip('.:;') or 'New conversation'


# Match explicit @ references without parsing prose (e.g. apostrophes) as shell syntax.
_IMAGE_REFERENCE = re.compile(r"""(?<!\S)@[ \t]*(?:"([^"\n]+)"|'([^'\n]+)'|([^\s]+))""")


def image_references(text):
    return [(match, next(group for group in match.groups() if group is not None))
            for match in _IMAGE_REFERENCE.finditer(text)
            if Path(next(group for group in match.groups() if group is not None)).suffix.lower()
            in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}]


def referenced_images(text):
    """Turn @image paths into image parts; leave ordinary @ agent text alone."""
    references = image_references(text)
    if not references:
        return text, None
    images = []
    for _, filename in references:
        _, parts = image_prompt('/image ' + shlex.quote(filename))
        images.append(parts[1])
    question = text
    for match, _ in reversed(references):
        question = question[:match.start()] + ' ' + question[match.end():]
    question = ' '.join(question.split()) or 'Describe this image.'
    return question, [{'type': 'text', 'text': question}, *images]


def image_mime(data):
    """Validate a supported image signature for file and clipboard inputs."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    elif data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp'
    else:
        raise ValueError('Choose a PNG, JPEG, or WebP image.')
