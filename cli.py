import os
import asyncio
import subprocess
import queue
import threading
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.filters import to_filter, Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, ConditionalContainer, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.styles import Style

from rich.console import Console
from glim.display import Markdown, ComposerConsole, StreamingMarkdown, command_display
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

from glim.clipboard import ClipboardAttachments, read_clipboard
from glim.agent import Agent
from glim.conversation import chat_history, estimate_tokens, image_prompt, image_references, referenced_images
from glim.lmstudio import LMStudio


class Glim:
    def __init__(self):
        self.console = Console()
        self.lm = LMStudio()

        self.models = []
        self.model = None
        self.messages = []
        self.conversation_name = "New conversation"
        self.title_attempted = False
        self.working = False
        self.working_detail = ""
        self.pending = queue.Queue()
        self.approval = None
        self.approval_choice = 1
        self.persistent_composer = False
        self.clipboard_attachments = ClipboardAttachments()
        self.clipboard_busy = False
        self.clipboard_status = ""

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
            "selected": "bold #1e1e2e bg:#89dceb",
            "context": "#a6e3a1",
            "context-high": "#f9e2af",
        })

        self.session = PromptSession(
            history=InMemoryHistory(),
            output=output,
            style=self.input_style,
            erase_when_done=True,
            reserve_space_for_menu=0,
            refresh_interval=0.15,
            key_bindings=self.approval_bindings(),
        )
        self.session.app.ttimeoutlen = 0.1

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
                ConditionalContainer(
                    Window(FormattedTextControl(self.approval_menu), height=3),
                    filter=Condition(lambda: self.approval is not None),
                ),
                Window(height=1, style="class:input-bar"),
                self.session.app.layout.container,
                Window(height=1, style="class:input-bar"),
                VSplit([
                    Window(FormattedTextControl(self.footer), wrap_lines=True,
                           dont_extend_height=True),
                    Window(FormattedTextControl(self.context_status), width=23,
                           height=1, align=WindowAlign.RIGHT),
                ]),
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
            return FormattedText([("class:approval", f"  Approval needed — choose below{suffix}")])
        if self.clipboard_busy or self.clipboard_status:
            return FormattedText([("class:metadata", "  " + (
                "Reading clipboard…" if self.clipboard_busy else self.clipboard_status))])
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
            return FormattedText([("class:placeholder", "1 / Y allow · 2 / N decline · ↑↓ then Enter")])
        return FormattedText([("class:placeholder", "Ask anything…  Ctrl+V / Alt+V paste  @ for tools  /help")])

    def context_status(self):
        limit = next((m.get("context_length") for m in self.models if m["id"] == self.model), None)
        if not isinstance(limit, int) or limit <= 0:
            return FormattedText([("class:metadata", "Context —  ")])
        usage = self.lm.context_usage
        if isinstance(usage, dict) and usage.get("model") == self.model:
            tokens, estimated = usage["tokens"], usage["estimated"]
        else:
            tokens, estimated = estimate_tokens(self.messages), bool(self.messages)
        percent = tokens * 100 / limit
        style = "class:context-high" if percent >= 80 else "class:context"
        return FormattedText([(style, f"Context {'~' if estimated else ''}{percent:.0f}% used  ")])

    def approval_menu(self):
        result = []
        for index, label in enumerate(("1. Allow once  [Y]", "2. Decline     [N / Esc]")):
            selected = index == self.approval_choice
            result.append(("class:selected" if selected else "class:metadata",
                           ("  › " if selected else "    ") + label + "\n"))
        result.append(("class:metadata", "    ↑↓ select · Enter confirm"))
        return FormattedText(result)

    def approval_bindings(self):
        bindings = KeyBindings()
        choosing = Condition(lambda: self.approval is not None and not self.session.default_buffer.text)
        for key, approved in (("1", True), ("y", True), ("Y", True), ("2", False), ("n", False), ("N", False)):
            def choose(event, approved=approved):
                self.answer_approval(approved)
            bindings.add(key, filter=choosing)(choose)

        @bindings.add("up", filter=choosing)
        @bindings.add("down", filter=choosing)
        def move(event):
            self.approval_choice = 1 - self.approval_choice

        @bindings.add("enter", filter=choosing)
        def accept(event):
            self.answer_approval(self.approval_choice == 0)

        @bindings.add("escape", filter=Condition(lambda: self.approval is not None), eager=True)
        def decline(event):
            self.answer_approval(False)

        @bindings.add('c-v', filter=Condition(lambda: self.approval is None))
        @bindings.add('escape', 'v', filter=Condition(lambda: self.approval is None))
        async def paste(event):
            await self.paste_clipboard(event.current_buffer)

        @bindings.add('enter', filter=Condition(lambda: self.clipboard_busy), eager=True)
        def wait_for_clipboard(event):
            # Keep an async paste attached to the draft that requested it.
            self.clipboard_status = 'Wait for clipboard paste to finish.'

        return bindings

    async def paste_clipboard(self, buffer):
        if self.clipboard_busy:
            return
        self.clipboard_busy = True
        self.clipboard_status = ''
        self.session.app.invalidate()
        try:
            kind, value = await asyncio.to_thread(read_clipboard)
            if kind == 'image':
                marker = self.clipboard_attachments.add(value)
                buffer.insert_text(' ' + marker + ' ')
                self.clipboard_status = f'{marker} attached · add a question and press Enter · delete the marker to remove'
            else:
                # Match bracketed text paste: insert, never submit pasted newlines.
                buffer.insert_text(value.replace('\r\n', '\n').replace('\r', '\n'))
        except (OSError, ValueError, KeyError) as error:
            self.clipboard_status = str(error)
        finally:
            self.clipboard_busy = False
            self.session.app.invalidate()

    def process_requests(self):
        while True:
            text = self.pending.get()
            try:
                if text is None:
                    attachments = getattr(self, 'clipboard_attachments', None)
                    if attachments is not None:
                        attachments.close()
                    return
                self.working = True
                self.session.app.invalidate()
                attachments = getattr(self, 'clipboard_attachments', None)
                if attachments is not None:
                    text = attachments.resolve(text)
                references = image_references(text)
                starts_with_image = bool(references and references[0][0].start() == 0)
                if text.startswith("@") and not starts_with_image:
                    self.handle_agent_request(text)
                else:
                    self.normal_chat(text)
                if not self.title_attempted and self.messages:
                    self.title_attempted = True
                    first = self.messages[0].get('content', '')
                    if isinstance(first, list):
                        first = ' '.join(p.get('text', '') for p in first if p.get('type') == 'text')
                    self.set_working_detail('Naming conversation')
                    try:
                        title = self.lm.generate_title(self.model, first)
                        if self.conversation_name == 'New conversation':
                            self.conversation_name = title
                    except Exception:
                        # Naming failure must never fail or replay a chat request.
                        pass
            except Exception as error:
                self.console.print(Text(f"Request failed: {error}", style="red"))
            finally:
                self.pending.task_done()
                self.working = False
                self.working_detail = ""
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
        self.console.print("\n[bold #89dceb]Glim help[/bold #89dceb]")
        self.console.print("Chat, work on your project, or search the web.\n")

        def section(title, rows):
            self.console.print(Text(title, style="bold #cba6f7"))
            if self.console.width < 60:
                for command, description in rows:
                    self.console.print(Text(command, style="bold #89dceb"))
                    self.console.print(Text("  " + description))
            else:
                table = Table.grid(padding=(0, 2))
                table.add_column(style="bold #89dceb", no_wrap=True)
                table.add_column()
                for command, description in rows:
                    table.add_row(Text(command), Text(description))
                self.console.print(table)
            self.console.print()

        section("Start here", [
            ("Ask a question", "Normal chat; no tools enabled."),
            ("@ inspect this project", "Use project tools for a task."),
            ("@ search the web for …", "Look up information online."),
            ("@ run python3 --version", "Run an exact shell command."),
        ])
        section("Commands", [
            ("/model", "Choose a model; keep conversation history."),
            ("/status", "View connection, model, and working directory."),
            ("/tools", "List the tools available with @."),
            ("/title NAME", "Rename this conversation."),
            ('/image "PATH" QUESTION', "Send a PNG, JPEG, or WebP image to a vision model."),
            ("/clear", "Clear chat and agent history."),
            ("/help", "Show this guide."),
            ("/exit", "Quit Glim."),
        ])
        section("Command approval", [
            ("1 or Y", "Allow this command once."),
            ("2, N, or Esc", "Decline the command."),
            ("↑ / ↓ then Enter", "Choose and confirm; Decline starts selected."),
        ])
        self.console.print("[bold #cba6f7]While Glim works[/bold #cba6f7]")
        self.console.print("Keep typing to queue a follow-up. Use slash commands after work finishes.")
        self.console.print("Approval shortcuts work when the input is empty.\n")
        self.console.print("[bold #cba6f7]Below the input[/bold #cba6f7]")
        self.console.print("Model · working path · conversation title; context usage on the right.")
        self.console.print("[cyan]~[/cyan] means estimated usage. [cyan]—[/cyan] means the context limit is unavailable.")
        self.console.print('[dim]Ctrl+V or Alt+V pastes a clipboard image or text. Delete an attachment marker to remove it.[/dim]')
        self.console.print('\n[dim]Attach images with @"/path/to/image.png" followed by your question.[/dim]')
        self.console.print("\n[dim]Chat and @ share history in this session. Restarting Glim clears it.[/dim]\n")

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
            self.approval_choice = 1
            self.approval = request
            self.console.print(Text("Allow this command? Choose 1 / Y to allow once or 2 / N to decline.", style="yellow"))
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
                "1 / Y: allow once · 2 / N: decline > "
            ).strip().lower()

        except (
            EOFError,
            KeyboardInterrupt,
        ):
            self.console.print()
            return False

        approved = answer in (
            "1",
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
            "[cyan]GLIM_SERVER_URL=http://HOST:PORT glim[/cyan]",
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
        self.lm.context_usage = None
        self.conversation_name = "New conversation"
        self.title_attempted = False

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

        try:
            if text.startswith('/image '):
                _, content = image_prompt(text)
            else:
                _, attachments = referenced_images(text)
                content = attachments if attachments is not None else text
        except (OSError, ValueError) as error:
            self.console.print(Text(str(error), style='red'))
            return
        user_message = {"role": "user", "content": content}
        self.messages.append(user_message)
        chunks = []
        generator = None
        rendered = None
        notice_shown = False
        try:
            generator = self.lm.stream_chat(self.model, chat_history(self.messages))
            self.set_chat_detail('Waiting for model')
            rendered = StreamingMarkdown(self.console)
            for chunk in generator:
                notice = self.lm.context_notice
                if isinstance(notice, str) and notice and not notice_shown:
                    self.console.print(Text(notice, style='yellow'))
                    notice_shown = True
                delta = chunk.get('choices', [{}])[0].get('delta') or {}
                if delta.get('reasoning_content') or delta.get('reasoning'):
                    self.set_chat_detail('Model is reasoning')
                answer = delta.get('content')
                if answer:
                    self.set_chat_detail('Receiving answer')
                    chunks.append(answer)
                    if rendered:
                        rendered.feed(answer)
            reason = self.lm.last_finish_reason
            if reason == 'length':
                self.console.print(Text(
                    'Generation reached the token/context limit. The reply may be incomplete. '
                    'Ask to continue, or increase the loaded context length.', style='yellow'))
            elif not chunks:
                self.console.print(Text(
                    'The model finished without an answer. It may have used its budget reasoning; '
                    'try a shorter request or disable thinking in LM Studio.', style='yellow'))
            if chunks:
                self.notify_done('Glim', 'Response complete' if reason != 'length' else 'Response reached limit')
        except Exception as error:
            self.console.print(Text(f'Request failed: {error}', style='red'))
        finally:
            if generator is not None:
                generator.close()
            if rendered:
                rendered.finish()
            elif chunks:
                self.console.print(Markdown(''.join(chunks)))
            self.console.print()
            if chunks:
                self.messages.append({'role': 'assistant', 'content': ''.join(chunks)})
            else:
                # Failed/empty requests must not grow history and poison follow-ups.
                if self.messages and self.messages[-1] is user_message:
                    self.messages.pop()
            self.set_chat_detail('')

    def set_chat_detail(self, detail):
        if getattr(self, 'session', None) is not None:
            self.set_working_detail(detail)

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
        if text == '/image' or text.startswith('/image '):
            if text == '/image':
                self.console.print(Text('Usage: /image "/path/to/image.png" What is in this image?', style='yellow'))
            else:
                self.print_user_message(text)
                self.pending.put(text)
            return True

        if text.startswith("/title "):
            self.title_attempted = True
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
                if not worker.is_alive():
                    self.clipboard_attachments.close()

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

            if self.approval and text.lower() in ("1", "2", "y", "yes", "n", "no"):
                self.answer_approval(text.lower() in ("1", "y", "yes"))
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

            self.clipboard_status = ''
            self.pending.put(text)
            if self.approval:
                self.console.print("[yellow]Message queued. Choose 1 / Y or 2 / N for the current command.[/yellow]")
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
