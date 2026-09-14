import os
import subprocess
import queue
import threading
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.filters import to_filter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.styles import Style

from rich.console import Console
from rich.live import Live
from glim.display import Markdown, ComposerConsole, StreamingMarkdown, command_display
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from glim.agent import Agent
from glim.conversation import chat_history
from glim.lmstudio import LMStudio


class Glim:
    def __init__(self):
        self.console = Console()
        self.lm = LMStudio()

        self.models = []
        self.model = None
        self.messages = []
        self.conversation_name = "New conversation"
        self.working = False
        self.working_detail = ""
        self.pending = queue.Queue()
        self.approval = None
        self.persistent_composer = False

        output = create_output()

        if hasattr(output, "enable_bell"):
            output.enable_bell = False

        self.input_style = Style.from_dict({
            "input-bar": "bg:#303030 #ffffff",
            "prompt": "bold cyan bg:#303030",
            "placeholder": "ansibrightblack bg:#303030",
            "metadata": "#888888",
            "model": "bold #89dceb",
            "path": "#a6e3a1",
            "conversation": "#cba6f7",
            "disconnected": "#f38ba8",
            "working": "bold cyan",
            "approval": "bold ansiyellow",
        })

        self.session = PromptSession(
            history=InMemoryHistory(),
            output=output,
            style=self.input_style,
            erase_when_done=True,
            reserve_space_for_menu=0,
            refresh_interval=0.15,
        )

        # PromptSession owns a real Window for the input buffer. Styling that
        # window paints its full available width without applying a background
        # to unrelated lines while the terminal redraws.
        for window in self.session.app.layout.find_all_windows():
            if getattr(window.content, "buffer", None) is self.session.default_buffer:
                window.style = "class:input-bar"
                window.dont_extend_height = to_filter(True)
                break

        # Keep padding inside the prompt surface, with no background applied
        # to unused terminal rows. Wrapped input still grows with its content.
        self.session.app.layout.container = HSplit(
            [
                Window(FormattedTextControl(self.working_status), height=1),
                Window(height=1, style="class:input-bar"),
                self.session.app.layout.container,
                Window(height=1, style="class:input-bar"),
                Window(FormattedTextControl(self.footer), wrap_lines=True,
                       dont_extend_height=True),
            ],
        )

    def footer(self):
        return FormattedText([
            ("class:metadata", "  "),
            ("class:model" if self.model else "class:disconnected", self.model or "No model"),
            ("class:metadata", " · "),
            ("class:path", self.short_cwd()),
            ("class:metadata", " · "),
            ("class:conversation", self.conversation_name),
        ])

    def working_status(self):
        queued = self.pending.qsize()
        suffix = f" · {queued} queued" if queued else ""
        if self.approval:
            return FormattedText([("class:approval", f"  Approval needed — paused: type y or n, then Enter{suffix}")])
        if self.working or queued:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = frames[int(time.monotonic() / 0.15) % len(frames)]
            detail = f" · {self.working_detail}" if self.working_detail else ""
            return FormattedText([("class:working", f"  {frame} Working…{detail}{suffix}")])
        return FormattedText([("class:metadata", "  Ready")])

    def set_working_detail(self, detail):
        self.working_detail = detail
        self.session.app.invalidate()

    def input_placeholder(self):
        if self.approval:
            return FormattedText([("class:placeholder", "Type y to approve or n to decline, then Enter")])
        return FormattedText([("class:placeholder", "Ask anything…  @ for project tools  /help for commands")])

    def process_requests(self):
        while True:
            text = self.pending.get()
            try:
                if text is None:
                    return
                self.working = True
                self.session.app.invalidate()
                if text.startswith("@"):
                    self.handle_agent_request(text)
                else:
                    self.normal_chat(text)
            except Exception as error:
                self.console.print(Text(f"Request failed: {error}", style="red"))
            finally:
                self.pending.task_done()
                self.working = False
                self.session.app.invalidate()

    # -----------------------------------------------------
    # GENERAL
    # -----------------------------------------------------

    def short_cwd(self):
        home = os.path.expanduser("~")
        cwd = os.getcwd()

        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]

        return cwd

    def detect_models(self):
        try:
            self.models = self.lm.get_loaded_models()

            if self.models:
                self.model = self.models[0]["id"]
                return True

            self.model = None
            return False

        except Exception:
            self.models = []
            self.model = None
            return False

    # -----------------------------------------------------
    # USER MESSAGE DISPLAY
    # -----------------------------------------------------

    def print_user_message(self, text):
        """Render submitted input in its distinct message surface."""
        self.console.print()

        message = Text()
        message.append("> ", style="bold cyan")
        message.append(text, style="white")
        self.console.print(Padding(
            message,
            (1, 2),
            style="on #303030",
            expand=True,
        ))

        self.console.print()

    # -----------------------------------------------------
    # DESKTOP NOTIFICATION
    # -----------------------------------------------------

    def notify_done(
        self,
        title="Glim",
        message="Response complete",
    ):
        try:
            subprocess.Popen(
                [
                    "notify-send",
                    "--app-name=Glim",
                    title,
                    message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        except Exception:
            pass

    # -----------------------------------------------------
    # HEADER
    # -----------------------------------------------------

    def print_header(self):
        content = Text("Glim", style="bold")
        content.append("\n\nType ", style="dim not bold")
        content.append("/model", style="bold #89dceb")
        content.append(" to find a local model.\nType ", style="dim not bold")
        content.append("/help", style="bold #89dceb")
        content.append(" for commands.", style="dim not bold")
        self.console.print()
        self.console.print(Panel.fit(content, border_style="#555555", padding=(1, 2)))
        self.console.print()

    # -----------------------------------------------------
    # HELP
    # -----------------------------------------------------

    def show_help(self):
        help_text = """
# Glim Help

## Chat

Type normally for lightweight chat.

No filesystem, shell, or web tools are attached.

Examples:

`hi`

`explain async/await`

`what does this Python error mean?`

## Agent mode

Start your request with `@`.

Agent mode temporarily gives the model access to project and web tools.

Examples:

`@ inspect this project`

`@ read package.json and explain the dependencies`

`@ fix the login bug`

`@ run the tests and fix failures`

`@ search the web for the latest Godot release notes`

## Direct shell command

Use:

`@ run COMMAND`

Example:

`@ run python3 --version`

Safe read-only commands may run immediately.

Other commands require your approval.

## Commands

`/help`

Show this help.

`/model`

Show or switch loaded LM Studio models.

`/status`

Show connection, model, directory, and context.

`/tools`

Show tools available in agent mode.

`/clear`

Clear normal chat history.

`/exit`

Quit Glim.

## Notifications

Glim sends a desktop notification only after an AI response
or agent task has completely finished.

Typing does not trigger notifications.

## Message display

Your submitted messages are shown in a subtle gray block so
they are easy to distinguish from AI responses.

## Modes

**Normal chat**

Your conversation goes directly to LM Studio without agent tools.

**@ Agent mode**

A small agent prompt and tool definitions are temporarily attached.

When the task finishes, Glim returns to lightweight chat.
"""

        self.console.print(
            Markdown(help_text)
        )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    def show_status(self):
        status = Text()

        status.append(
            f"{self.lm.server_name}: "
        )

        if self.model:
            status.append(
                "connected",
                style="green",
            )
        else:
            status.append(
                "disconnected",
                style="red",
            )

        status.append(
            "\nModel: "
        )

        status.append(
            self.model or "None",
            style="cyan",
        )

        status.append(
            "\nDirectory: "
        )

        status.append(
            self.short_cwd()
        )

        status.append(
            "\nChat context messages: "
        )

        status.append(
            str(len(self.messages))
        )

        status.append(
            "\nAgent tools: "
        )

        status.append(
            "available with @",
            style="yellow",
        )

        self.console.print()
        self.console.print(status)
        self.console.print()

    # -----------------------------------------------------
    # TOOLS
    # -----------------------------------------------------

    def show_tools(self):
        self.console.print()

        self.console.print(
            "[bold]Agent tools[/bold]"
        )

        self.console.print()

        self.console.print(
            "  [cyan]list_files[/cyan]   "
            "List project files and directories"
        )

        self.console.print(
            "  [cyan]read_file[/cyan]    "
            "Read project files"
        )

        self.console.print(
            "  [cyan]write_file[/cyan]   "
            "Create or modify files (with an added/removed diff)"
        )

        self.console.print(
            "  [cyan]run_command[/cyan]  "
            "Run shell commands"
        )

        self.console.print(
            "  [cyan]web_search[/cyan]   "
            "Search the public web"
        )

        self.console.print(
            "  [cyan]fetch_url[/cyan]    "
            "Read a public web page"
        )

        self.console.print()

        self.console.print(
            "[dim]Tools are attached only when "
            "a request starts with @.[/dim]"
        )

        self.console.print()

    # -----------------------------------------------------
    # COMMAND APPROVAL
    # -----------------------------------------------------

    def confirm_command(self, command):
        if self.persistent_composer:
            event = threading.Event()
            request = {"event": event, "approved": False}
            self.approval = request
            self.console.print(Text("Allow this command? Type y or n below.", style="yellow"))
            self.console.print(command_display(command))
            self.session.app.invalidate()
            event.wait()
            return request["approved"]

        self.console.print()

        self.console.print(
            "[bold yellow]This command requires approval:[/bold yellow]"
        )

        self.console.print(command_display(command))

        self.console.print()

        try:
            answer = self.session.prompt(
                "Allow this command? [y/N] "
            ).strip().lower()

        except (
            EOFError,
            KeyboardInterrupt,
        ):
            self.console.print()
            return False

        approved = answer in (
            "y",
            "yes",
        )

        if approved:
            self.console.print(
                "[green]Approved.[/green]"
            )

        else:
            self.console.print(
                "[yellow]Declined.[/yellow]"
            )

        return approved

    # -----------------------------------------------------
    # MODELS
    # -----------------------------------------------------

    def show_model_setup(self, error=None):
        lines = [
            f"[bold]No model is available from {self.lm.server_name}.[/bold]",
            "",
            "[bold]LM Studio setup[/bold]",
            "1. Open LM Studio and load a chat model.",
            "2. Open the Developer tab and turn on [cyan]Start server[/cyan].",
            "   Or run [cyan]lms server start[/cyan] in a terminal.",
            "3. Return here and type [cyan]/model[/cyan] again.",
            "",
            "[bold]Another local server[/bold]",
            "Point Glim at an OpenAI-compatible server:",
            "[cyan]GLIM_SERVER_URL=http://HOST:PORT/v1 glim[/cyan]",
            "Optionally label it with [cyan]GLIM_SERVER_NAME[/cyan].",
        ]

        if error:
            lines.extend([
                "",
                f"[dim]{error}[/dim]",
            ])

        self.console.print()
        self.console.print(Panel(
            "\n".join(lines),
            title="Model connection",
            border_style="yellow",
        ))
        self.console.print()

    def show_models(self):
        try:
            models = self.lm.get_loaded_models()

        except Exception as error:
            self.show_model_setup(error)
            return

        if not models:
            self.show_model_setup()
            return

        self.models = models

        self.console.print()
        self.console.print(
            (
                "[bold]Running models[/bold]"
                if self.lm.has_loaded_state
                else "[bold]Available models[/bold]"
            )
        )
        self.console.print()

        for index, model in enumerate(
            models,
            start=1,
        ):
            marker = (
                "●"
                if model["id"] == self.model
                else " "
            )

            details = []

            if model.get("params"):
                details.append(
                    model["params"]
                )

            if model.get("architecture"):
                details.append(
                    model["architecture"]
                )

            detail_text = ""

            if details:
                detail_text = (
                    " [dim]("
                    + " · ".join(details)
                    + ")[/dim]"
                )

            self.console.print(
                f"  {marker} {index}. "
                f"[bold]{model['name']}[/bold]"
                f"{detail_text}"
            )

            if model["name"] != model["id"]:
                self.console.print(
                    f"      [dim]"
                    f"{model['id']}"
                    f"[/dim]"
                )

        self.console.print()

        try:
            choice = (
                self.session.prompt(
                    "Select model number, "
                    "or press Enter to cancel: "
                )
                .strip()
            )

        except (
            EOFError,
            KeyboardInterrupt,
        ):
            self.console.print()
            return

        if not choice:
            self.console.print()
            return

        if not choice.isdigit():
            self.console.print(
                "\n[red]Invalid selection.[/red]\n"
            )
            return

        index = int(choice) - 1

        if index < 0 or index >= len(models):
            self.console.print(
                "\n[red]Invalid selection.[/red]\n"
            )
            return

        selected = models[index]

        self.model = selected["id"]

        self.console.print(
            f"\n[green]Switched to "
            f"{self.model}[/green]"
        )

        self.console.print(
            "[dim]Conversation context retained.[/dim]\n"
        )

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    def clear(self):
        self.messages.clear()
        self.conversation_name = "New conversation"

        os.system(
            "clear"
            if os.name != "nt"
            else "cls"
        )

        self.print_header()

    # -----------------------------------------------------
    # NORMAL CHAT
    # -----------------------------------------------------

    def normal_chat(
        self,
        text,
    ):
        if not self.model:
            self.console.print(
                "\n[red]No active model.[/red]"
            )
            return

        self.messages.append(
            {
                "role": "user",
                "content": text,
            }
        )

        chunks = []

        try:
            generator = self.lm.stream_chat(
                self.model,
                chat_history(self.messages),
            )

            first_text = None

            with self.console.status(
                "[dim]Thinking...[/dim]",
                spinner="dots",
            ):
                for chunk in generator:
                    delta = (
                        chunk
                        .get(
                            "choices",
                            [{}],
                        )[0]
                        .get(
                            "delta",
                            {},
                        )
                    )

                    content = delta.get(
                        "content"
                    )

                    if content:
                        first_text = content
                        break

            if first_text is None:
                self.console.print(
                    "[yellow](No response)[/yellow]\n"
                )
                return

            chunks.append(first_text)
            if getattr(self, "persistent_composer", False):
                rendered = StreamingMarkdown(self.console)
                rendered.feed(first_text)
                try:
                    for chunk in generator:
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        if content:
                            chunks.append(content)
                            rendered.feed(content)
                finally:
                    rendered.finish()
                    self.console.print()
                    self.messages.append({"role": "assistant", "content": "".join(chunks)})
                self.notify_done()
                return
            try:
                # Reparse the accumulated Markdown as tokens arrive, so split
                # fences, lists, and emphasis become formatted when complete.
                with Live(
                    Markdown(first_text),
                    console=self.console,
                    refresh_per_second=8,
                    transient=True,
                    vertical_overflow="ellipsis",
                ) as response:
                    for chunk in generator:
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            chunks.append(content)
                            response.update(Markdown("".join(chunks)))
            finally:
                # Commit the full answer to scrollback after removing the
                # viewport-sized preview, including partial answers on failure.
                self.console.print(Markdown("".join(chunks)))
                self.console.print()

            assistant_text = "".join(
                chunks
            )

            self.messages.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                }
            )

            self.notify_done(
                "Glim",
                "Response complete",
            )

        except Exception as error:
            self.console.print()

            self.console.print(
                f"[red]Request failed:[/red] "
                f"{error}"
            )

            self.console.print()

    # -----------------------------------------------------
    # AGENT MODE
    # -----------------------------------------------------

    def handle_agent_request(
        self,
        text,
    ):
        task = (
            text[1:]
            .strip()
        )

        if not task:
            self.console.print(
                "\n[yellow]Usage:[/yellow] "
                "@ <project or web task>\n"
            )
            return

        if not self.model:
            self.console.print(
                "\n[red]No active model.[/red]\n"
            )
            return

        self.console.print(
            "[bold yellow]Agent[/bold yellow]"
        )

        agent = Agent(
            self.lm,
            self.model,
            self.console,
            confirm_callback=self.confirm_command,
        )

        try:
            result = agent.run(task, history=self.messages)

            self.console.print()

            if result:
                self.console.print(
                    Markdown(
                        result
                    )
                )

            self.console.print()

            self.notify_done(
                "Glim Agent",
                "Task complete",
            )

        except Exception as error:
            self.console.print()

            self.console.print(
                f"[red]Agent failed:[/red] "
                f"{error}"
            )

            self.console.print()

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

    def handle_command(
        self,
        text,
    ):
        if text.startswith("/title "):
            self.conversation_name = " ".join(text[7:].split()) or "New conversation"
            return True

        if text == "/help":
            self.show_help()
            return True

        if text in (
            "/model",
            "/models",
        ):
            self.show_models()
            return True

        if text == "/status":
            self.show_status()
            return True

        if text == "/tools":
            self.show_tools()
            return True

        if text == "/clear":
            self.clear()
            return True

        if text in (
            "/exit",
            "/quit",
        ):
            raise SystemExit

        return False

    # -----------------------------------------------------
    # MAIN LOOP
    # -----------------------------------------------------

    def run(self):
        self.detect_models()
        self.print_header()
        self.persistent_composer = True
        with patch_stdout(raw=True):
            self.console = ComposerConsole(status_callback=self.set_working_detail)
            worker = threading.Thread(target=self.process_requests, daemon=True)
            worker.start()
            try:
                self.run_input_loop()
            finally:
                if self.approval:
                    self.answer_approval(False)
                self.pending.put(None)
                worker.join(timeout=1)

    def run_input_loop(self):

        while True:
            try:
                text = (
                    self.session.prompt(
                        FormattedText([
                            ("class:prompt", "  > "),
                        ]),
                        placeholder=self.input_placeholder,
                    )
                    .strip()
                )

            except KeyboardInterrupt:
                if self.approval:
                    self.answer_approval(False)
                self.console.print(
                    "\n[dim]Use /exit to quit.[/dim]\n"
                )

                continue

            except EOFError:
                if self.approval:
                    self.answer_approval(False)
                if self.working or self.pending.unfinished_tasks:
                    self.console.print("[yellow]Wait for the active work before exiting.[/yellow]")
                    continue
                self.console.print()
                break

            if not text:
                continue

            if self.approval and text.lower() in ("y", "yes", "n", "no"):
                self.answer_approval(text.lower() in ("y", "yes"))
                continue

            if text.startswith("/"):
                if self.working or self.pending.unfinished_tasks:
                    self.console.print("[yellow]Commands are available after the queued work finishes.[/yellow]")
                    continue
                if not self.handle_command(
                    text
                ):
                    self.console.print(
                        f"\n[red]Unknown command:[/red] "
                        f"{text}"
                    )

                    self.console.print(
                        "[dim]Type /help for available commands.[/dim]\n"
                    )

                continue

            self.print_user_message(
                text
            )

            if self.conversation_name == "New conversation":
                title = " ".join(text.lstrip("@").split())
                self.conversation_name = title[:47] + ("…" if len(title) > 47 else "")
            self.pending.put(text)
            if self.approval:
                self.console.print("[yellow]Message queued. The current command still needs y or n to continue.[/yellow]")
            self.session.app.invalidate()

    def answer_approval(self, approved):
        request = self.approval
        self.approval = None
        request["approved"] = approved
        self.console.print(Text("Command approved — running…" if approved else "Command declined — continuing…", style="cyan"))
        request["event"].set()
        self.session.app.invalidate()


def main():
    Glim().run()
