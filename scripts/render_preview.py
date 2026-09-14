"""Render the README preview with Glim's actual message and Markdown renderers.

Run after installing Glim: python scripts/render_preview.py
The response is fixed sample content; no model server or image generator is used.
"""

import io
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import TerminalTheme

from glim.cli import Glim
from glim.display import StreamingMarkdown


def main():
    console = Console(record=True, width=88, file=io.StringIO())
    app = Glim.__new__(Glim)
    app.console = console
    app.print_header()
    app.print_user_message("How do I read a JSON file in Python?")
    response = StreamingMarkdown(console)
    response.feed(
        "Use `json.load()` to read a file into a Python object.\n\n"
        "```python\n"
        "import json\n\n"
        'with open("config.json", encoding="utf-8") as file:\n'
        "    config = json.load(file)\n\n"
        'print(config["name"])\n'
        "```\n\n"
        "For JSON that is already a string, use `json.loads()` instead.\n"
    )
    response.finish()
    app.print_user_message("@ inspect this project and explain its structure")
    theme = TerminalTheme(
        (24, 24, 27), (228, 228, 231),
        [(39, 39, 42), (243, 139, 168), (166, 227, 161), (249, 226, 175),
         (137, 180, 250), (203, 166, 247), (137, 220, 235), (228, 228, 231)],
    )
    output = Path(__file__).resolve().parents[1] / "docs" / "assets" / "terminal-preview.svg"
    output.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(output), title="Glim · sample conversation", theme=theme,
                     unique_id="glim-preview")
    print(output)


if __name__ == "__main__":
    main()
