"""Read desktop clipboard on explicit paste; keep attachments for this session."""
import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from glim.conversation import image_references, image_mime

MAX_BYTES = 20 * 1024 * 1024


def _run(command):
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=10, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('Clipboard timed out. Copy the image again and retry.') from error
    if result.returncode:
        raise ValueError('Could not read the desktop clipboard. Copy an image or text and retry.')
    if len(result.stdout) > MAX_BYTES * 2:
        raise ValueError('Clipboard content is too large (20 MB image limit).')
    return result.stdout


def _linux():
    if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-paste'):
        types = _run(['wl-paste', '--list-types']).decode('utf-8', errors='replace').splitlines()
        def read(mime):
            return _run(['wl-paste', '--no-newline', '--type', mime])
    elif os.environ.get('DISPLAY') and shutil.which('xclip'):
        types = _run(['xclip', '-selection', 'clipboard', '-o', '-t', 'TARGETS']).decode('utf-8', errors='replace').splitlines()
        def read(mime):
            return _run(['xclip', '-selection', 'clipboard', '-o', '-t', mime])
    else:
        raise ValueError('Clipboard paste needs wl-clipboard on Wayland or xclip on X11, '
                         'and access to your desktop session. You can still attach an @image path.')
    for mime in ('image/png', 'image/jpeg', 'image/webp'):
        if mime in types:
            return 'image', read(mime)
    if any(mime.startswith('image/') for mime in types):
        raise ValueError('Copy the image as PNG, JPEG, or WebP.')
    for mime in ('text/plain;charset=utf-8', 'UTF8_STRING', 'text/plain', 'STRING'):
        if mime in types:
            return 'text', read(mime).decode('utf-8', errors='replace')
    raise ValueError('No image or text is available in the clipboard.')


def _windows(executable):
    # STA is required by System.Windows.Forms. No clipboard data is executed.
    script = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if ([System.Windows.Forms.Clipboard]::ContainsImage()) {
    $image = [System.Windows.Forms.Clipboard]::GetImage()
    $stream = New-Object System.IO.MemoryStream
    try {
        $image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        @{kind='image'; data=[Convert]::ToBase64String($stream.ToArray())} | ConvertTo-Json -Compress
    } finally { $stream.Dispose(); $image.Dispose() }
} elseif ([System.Windows.Forms.Clipboard]::ContainsText()) {
    @{kind='text'; data=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
        [System.Windows.Forms.Clipboard]::GetText()))} | ConvertTo-Json -Compress
} else { throw 'No image or text in clipboard' }
'''
    result = json.loads(_run([executable, '-NoProfile', '-NonInteractive', '-STA', '-Command', script]))
    data = base64.b64decode(result['data'], validate=True)
    return result['kind'], data if result['kind'] == 'image' else data.decode('utf-8')


def _mac():
    with tempfile.TemporaryDirectory(prefix='glim-paste-') as folder:
        filename = str(Path(folder)/'clipboard.png')
        script = ('set outputFile to open for access POSIX file ' + json.dumps(filename) + ' with write permission\n'
                  'try\nset eof outputFile to 0\nwrite (the clipboard as «class PNGf») to outputFile\n'
                  'close access outputFile\non error\nclose access outputFile\nerror "No clipboard image"\nend try')
        try:
            _run(['osascript', '-e', script])
        except ValueError:
            return 'text', _run(['osascript', '-e', 'get the clipboard as text']).decode('utf-8').removesuffix('\n')
        return 'image', Path(filename).read_bytes()


def read_clipboard():
    if sys.platform == 'win32':
        return _windows(shutil.which('powershell.exe') or 'powershell.exe')
    if sys.platform == 'darwin':
        return _mac()
    if 'microsoft' in os.uname().release.lower() and shutil.which('powershell.exe'):
        return _windows(shutil.which('powershell.exe'))
    return _linux()


class ClipboardAttachments:
    def __init__(self):
        self.directory = None
        self.paths = {}

    def add(self, data):
        if len(data) > MAX_BYTES:
            raise ValueError('Image must be 20 MB or smaller.')
        mime = image_mime(data)
        suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[mime]
        if self.directory is None:
            self.directory = tempfile.TemporaryDirectory(prefix='glim-clipboard-')
        name = f'clipboard-{len(self.paths) + 1}.{suffix}'
        path = Path(self.directory.name)/name
        path.write_bytes(data)
        self.paths[name] = path
        return '@' + name

    def resolve(self, text):
        for match, name in reversed(image_references(text)):
            if name in self.paths:
                text = text[:match.start()] + '@' + shlex.quote(str(self.paths[name])) + text[match.end():]
        return text

    def close(self):
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
        self.paths.clear()
