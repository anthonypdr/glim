"""Terminal rendering shared by chat and tool responses."""

from contextlib import contextmanager
import re

from rich.console import Console, Group
from rich.markdown import Markdown as RichMarkdown, CodeBlock
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text


CODE_BACKGROUND = "#282c34"


def code_panel(content, title):
    label = title.copy() if isinstance(title, Text) else Text(str(title))
    label.stylize("dim")
    return Padding(Group(label, Text(""), content), (1, 2),
                   style=f"on {CODE_BACKGROUND}", expand=False)


def command_display(command):
    command = str(command)
    highlighted = Syntax(command, "bash", theme="monokai",
                         background_color=CODE_BACKGROUND).highlight(command)
    executable = re.match(r"\s*\S+", command)
    if executable:
        highlighted.stylize("bold #89dceb", executable.start(), executable.end())
    for flag in re.finditer(r"(?<!\S)--?[A-Za-z][\w-]*", command):
        highlighted.stylize("#f9e2af", flag.start(), flag.end())
    return code_panel(highlighted, "Command")


class FramedCodeBlock(CodeBlock):
    def __rich_console__(self, console, options):
        yield code_panel(
            Syntax(str(self.text).rstrip("\n"), self.lexer_name,
                   theme="monokai", word_wrap=True, background_color=CODE_BACKGROUND,
                   line_numbers=False, tab_size=4, indent_guides=False),
            self.lexer_name,
        )


def diff_panel(diff, path):
    """Render highlighted changes without borders or fixed-width gutters."""
    rows = []
    old_line = new_line = None
    for line in diff.splitlines():
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            old_line, new_line = map(int, hunk.groups())
            rows.append(("", "", line, "meta"))
        elif old_line is not None and line[:1] in ("+", "-", " "):
            marker = line[0]
            old = str(old_line) if marker != "+" else ""
            new = str(new_line) if marker != "-" else ""
            rows.append((old, new, line, marker))
            old_line += marker != "+"
            new_line += marker != "-"
        else:
            rows.append(("", "", line, "meta"))

    source = "\n".join(line[1:] if kind != "meta" else "" for _, _, line, kind in rows)
    lexer = Syntax.guess_lexer(str(path), source)
    highlighted = Syntax(source, lexer, theme="monokai", tab_size=4).highlight(source.expandtabs(4))
    lines = highlighted.split("\n", allow_blank=True)
    rendered = Text(overflow="fold")
    for index, (old, new, line, kind) in enumerate(rows):
        if kind == "meta":
            code = Text(line, style="dim cyan")
            style = ""
        else:
            color = "#a6e3a1" if kind == "+" else "#f38ba8" if kind == "-" else "#8993a4"
            code = Text(kind, style=color)
            code.append_text(lines[index])
            style = "on #253b30" if kind == "+" else "on #402b32" if kind == "-" else ""
        if style:
            code.stylize_before(style)
        rendered.append_text(code)
        rendered.append("\n")
    return code_panel(rendered, Text(str(path)))


class Markdown(RichMarkdown):
    elements = {**RichMarkdown.elements, "fence": FramedCodeBlock,
                "code_block": FramedCodeBlock}


class ComposerConsole(Console):
    # The composer supplies working status. Live cursor animations would compete
    # with prompt_toolkit, which owns the editable composer.
    def __init__(self, *args, status_callback=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.status_callback = status_callback

    @contextmanager
    def status(self, status, **kwargs):
        if self.status_callback:
            self.status_callback(Text.from_markup(str(status)).plain)
        try:
            yield
        finally:
            if self.status_callback:
                self.status_callback("")


class StreamingMarkdown:
    """Commit complete paragraphs and fenced blocks above the active prompt."""

    def __init__(self, console):
        self.console = console
        self.pending = ""
        self.block = ""
        self.fence = None
        self.separator = False

    def feed(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if self.separator and line.strip() and not self.fence:
                list_marker = r"^ {0,3}(?:[-+*]|\d+[.)])\s"
                list_continues = re.match(list_marker, self.block) and (
                    re.match(list_marker, line) or line.startswith(("  ", "\t"))
                )
                if not list_continues:
                    self.flush_block()
                self.separator = False
            self.block += line + "\n"
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if self.fence:
                if (marker and marker[1][0] == self.fence[0]
                        and len(marker[1]) >= len(self.fence) and not marker[2].strip()):
                    self.fence = None
                    self.flush_block()
            elif marker:
                self.fence = marker[1]
            elif not line.strip():
                # Wait for the next line so loose lists remain a single
                # Markdown block and retain numbering and nested indentation.
                self.separator = True

    def flush_block(self):
        if self.block.strip():
            self.console.print(Markdown(self.block))
            self.console.print()
        self.block = ""

    def finish(self):
        if self.pending:
            self.feed("\n")
        self.flush_block()
