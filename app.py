import os
import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import (
    Center,
    Horizontal,
    Vertical,
    VerticalScroll,
)
from textual.widgets import (
    Input,
    Markdown,
    Static,
)

from glim.lmstudio import LMStudio


class ChatMessage(Vertical):
    CSS = """
    ChatMessage {
        width: 100%;
        height: auto;
        margin-bottom: 2;
    }

    .message-role {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    .user-role {
        color: $primary;
    }

    .assistant-role {
        color: $success;
    }

    .message-body {
        width: 100%;
        height: auto;
        background: transparent;
    }

    .user-message-body {
        background: #303030;
        padding: 1 2;
        border: round #555555;
    }
    """

    def __init__(
        self,
        role,
        content="",
        message_id=None,
    ):
        super().__init__(id=message_id)

        self.role = role
        self.content = content

    def compose(self) -> ComposeResult:
        role_name = (
            "You"
            if self.role == "user"
            else "AI"
        )

        role_class = (
            "user-role"
            if self.role == "user"
            else "assistant-role"
        )

        yield Static(
            role_name,
            classes=f"message-role {role_class}",
        )

        yield Markdown(
            self.content,
            classes=f"message-body {self.role}-message-body",
        )

    def update_content(self, content):
        self.content = content

        markdown = self.query_one(
            ".message-body",
            Markdown,
        )

        markdown.update(content)


class GlimApp(App):
    TITLE = "Glim"

    CSS = """
    Screen {
        background: $background;
        color: $text;
    }

    #topbar {
        height: 3;
        padding: 1 3;
        background: transparent;
    }

    #brand {
        width: 1fr;
        text-style: bold;
    }

    #model-label {
        width: auto;
        color: $text-muted;
    }

    #chat-wrapper {
        width: 100%;
        height: 1fr;
        align-horizontal: center;
    }

    #chat {
        width: 90%;
        max-width: 100;
        height: 1fr;
        padding: 2 2 1 2;
        scrollbar-size: 1 1;
    }

    #welcome {
        width: 100%;
        height: auto;
        content-align: center middle;
        color: $text-muted;
        padding: 3 1;
    }

    #composer-wrapper {
        width: 100%;
        height: auto;
        align-horizontal: center;
        padding: 0 0 1 0;
    }

    #composer {
        width: 90%;
        max-width: 100;
        height: 5;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }

    #prompt {
        width: 100%;
        height: 1;
        border: none;
        background: transparent;
    }

    #bottom-wrapper {
        width: 100%;
        height: 2;
        align-horizontal: center;
    }

    #bottom {
        width: 90%;
        max-width: 100;
        height: 2;
        color: $text-muted;
    }

    #cwd {
        width: 1fr;
    }

    #status {
        width: auto;
        text-align: right;
    }

    .connected {
        color: $success;
    }

    .disconnected {
        color: $error;
    }
    """

    BINDINGS = [
        Binding(
            "ctrl+c",
            "quit",
            "Quit",
            show=False,
        ),
        Binding(
            "ctrl+l",
            "clear_chat",
            "Clear",
            show=False,
        ),
        Binding("escape", "interrupt", "Stop", show=True),
    ]

    def __init__(self):
        super().__init__()

        self.lm = LMStudio()

        self.models = []
        self.model = None

        self.messages = []

        self.message_counter = 0
        self.generating = False
        self.pending_messages = []
        self.cancel_event = threading.Event()
        self.active_response = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(
                "Glim",
                id="brand",
            )

            yield Static(
                "connecting...",
                id="model-label",
            )

        with Center(id="chat-wrapper"):
            yield VerticalScroll(
                Static(
                    "Local AI, without the unnecessary baggage.",
                    id="welcome",
                ),
                id="chat",
            )

        with Center(id="composer-wrapper"):
            with Vertical(id="composer"):
                yield Input(
                    placeholder=(
                        "Ask anything...   "
                        "/model   /clear   /status"
                    ),
                    id="prompt",
                )

        with Center(id="bottom-wrapper"):
            with Horizontal(id="bottom"):
                yield Static(
                    self.short_cwd(),
                    id="cwd",
                )

                yield Static(
                    "LM Studio  ○",
                    id="status",
                )

    def on_mount(self):
        self.query_one(
            "#prompt",
            Input,
        ).focus()

        self.detect_models()

    def short_cwd(self):
        home = os.path.expanduser("~")
        cwd = os.getcwd()

        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]

        return cwd

    # -----------------------------------------------------
    # CONNECTION / MODEL
    # -----------------------------------------------------

    @work(thread=True)
    def detect_models(self):
        try:
            models = self.lm.get_models()

            loaded = [
                model
                for model in models
                if model["loaded"]
            ]

            self.call_from_thread(
                self.models_detected,
                models,
                loaded,
            )

        except Exception as error:
            self.call_from_thread(
                self.connection_failed,
                str(error),
            )

    def models_detected(
        self,
        models,
        loaded,
    ):
        self.models = loaded

        if loaded:
            self.model = loaded[0]["id"]

            self.query_one(
                "#model-label",
                Static,
            ).update(self.model)

            self.query_one(
                "#status",
                Static,
            ).update(
                "LM Studio  ●"
            )

            self.query_one(
                "#status",
                Static,
            ).set_classes(
                "connected"
            )

        else:
            self.model = None

            self.query_one(
                "#model-label",
                Static,
            ).update(
                "no model loaded"
            )

            self.query_one(
                "#status",
                Static,
            ).update(
                "LM Studio  ○"
            )

            self.query_one(
                "#status",
                Static,
            ).set_classes(
                "disconnected"
            )

    def connection_failed(self, error):
        self.model = None
        self.models = []

        self.query_one(
            "#model-label",
            Static,
        ).update(
            "disconnected"
        )

        self.query_one(
            "#status",
            Static,
        ).update(
            "LM Studio  ○"
        )

        self.query_one(
            "#status",
            Static,
        ).set_classes(
            "disconnected"
        )

        self.add_system_message(
            "Could not connect to LM Studio.\n\n"
            "Make sure the server is running on "
            "`127.0.0.1:1234`."
        )

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

    def action_clear_chat(self):
        self.messages.clear()

        chat = self.query_one(
            "#chat",
            VerticalScroll,
        )

        chat.remove_children()

        chat.mount(
            Static(
                "Conversation cleared.",
                id="welcome",
            )
        )

    def handle_model_command(self, text):
        if not self.models:
            self.add_system_message(
                "No loaded LLMs were detected in LM Studio."
            )
            return

        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            choices = "\n".join(
                f"{index}. `{model['name']}`"
                for index, model in enumerate(self.models, start=1)
            )
            self.add_system_message(
                f"**Loaded models**\n\n{choices}\n\nUse `/model N` to switch."
            )
            return

        try:
            selected = self.models[int(parts[1]) - 1]["id"]
        except (ValueError, IndexError):
            self.add_system_message("Choose a valid model number.")
            return

        self.model_selected(selected)

    def model_selected(self, selected):
        if not selected:
            self.query_one("#prompt", Input).focus()
            return

        if selected == self.model:
            self.query_one("#prompt", Input).focus()
            return

        self.model = selected
        self.messages.clear()

        self.query_one(
            "#model-label",
            Static,
        ).update(selected)

        self.add_system_message(
            f"Switched to `{selected}`.\n\n"
            "Conversation context was cleared."
        )

        self.query_one(
            "#prompt",
            Input,
        ).focus()

    def show_status(self):
        model = self.model or "None"

        self.add_system_message(
            f"**LM Studio:** "
            f"{'Connected' if self.model else 'Disconnected'}\n\n"
            f"**Model:** `{model}`\n\n"
            f"**Directory:** `{self.short_cwd()}`\n\n"
            f"**Context messages:** {len(self.messages)}"
        )

    # -----------------------------------------------------
    # CHAT UI
    # -----------------------------------------------------

    async def remove_welcome(self):
        welcome = self.query(
            "#welcome"
        )

        if welcome:
            await welcome.remove()

    async def add_message(
        self,
        role,
        content,
    ):
        await self.remove_welcome()

        self.message_counter += 1

        message_id = (
            f"message-{self.message_counter}"
        )

        message = ChatMessage(
            role,
            content,
            message_id=message_id,
        )

        chat = self.query_one(
            "#chat",
            VerticalScroll,
        )

        await chat.mount(message)

        chat.scroll_end(
            animate=False,
        )

        return message

    def add_system_message(
        self,
        content,
    ):
        async def mount_message():
            await self.add_message(
                "assistant",
                content,
            )

        self.run_worker(
            mount_message(),
            exclusive=False,
        )

    # -----------------------------------------------------
    # INPUT
    # -----------------------------------------------------

    async def on_input_submitted(
        self,
        event: Input.Submitted,
    ):
        text = event.value.strip()

        if not text:
            return

        event.input.value = ""

        if text == "/exit":
            self.exit()
            return

        if text == "/clear":
            if self.generating:
                self.add_system_message(
                    "Stop the active response before clearing the chat."
                )
                return
            self.action_clear_chat()
            return

        if text == "/models" or text.startswith("/model"):
            self.handle_model_command(text)
            return

        if text == "/status":
            self.show_status()
            return

        if text.startswith("@"):
            await self.add_message(
                "assistant",
                "Agent mode is not implemented yet."
            )

            return

        if not self.model:
            await self.add_message(
                "assistant",
                "No model is currently loaded in LM Studio."
            )

            return

        if self.generating:
            # The user sees their queued message immediately. It is added to
            # API context only when its turn begins, preserving message order.
            await self.add_message("user", text)
            self.pending_messages.append(text)
            self.update_working_status()
            return

        await self.add_message(
            "user",
            text,
        )

        await self.start_generation(text)

    async def start_generation(self, text):
        self.messages.append(
            {
                "role": "user",
                "content": text,
            }
        )

        assistant = await self.add_message(
            "assistant",
            "",
        )

        self.generating = True
        self.cancel_event.clear()
        self.active_response = None
        self.update_working_status()

        self.generate_response(
            assistant.id,
        )

    def update_working_status(self):
        queued = len(self.pending_messages)
        suffix = f" · {queued} queued" if queued else ""
        self.query_one("#status", Static).update(
            f"Working · Esc to stop{suffix}"
        )

    def action_interrupt(self):
        if not self.generating:
            return

        self.cancel_event.set()
        if self.active_response is not None:
            self.active_response.close()
        self.query_one("#status", Static).update("Stopping…")

    def _store_response(self, response):
        self.active_response = response

    # -----------------------------------------------------
    # GENERATION
    # -----------------------------------------------------

    @work(thread=True)
    def generate_response(
        self,
        message_id,
    ):
        collected = ""

        try:
            for chunk in self.lm.stream_chat(
                self.model,
                self.messages,
                cancel_event=self.cancel_event,
                on_response=self._store_response,
            ):
                delta = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                )
                content = delta.get("content")

                if not content:
                    continue

                collected += content

                self.call_from_thread(
                    self.update_stream,
                    message_id,
                    collected,
                )

            self.call_from_thread(
                self.generation_finished,
                collected,
                self.cancel_event.is_set(),
            )

        except Exception as error:
            if self.cancel_event.is_set():
                self.call_from_thread(
                    self.generation_finished,
                    collected,
                    True,
                )
                return
            self.call_from_thread(
                self.generation_error,
                message_id,
                str(error),
            )

    def update_stream(
        self,
        message_id,
        content,
    ):
        try:
            message = self.query_one(
                f"#{message_id}",
                ChatMessage,
            )

            message.update_content(
                content
            )

            self.query_one(
                "#chat",
                VerticalScroll,
            ).scroll_end(
                animate=False,
            )

        except Exception:
            pass

    def generation_finished(
        self,
        content,
        interrupted=False,
    ):
        if content:
            self.messages.append(
                {
                    "role": "assistant",
                    "content": content,
                }
            )

        self.generating = False
        self.active_response = None

        self.query_one("#status", Static).update(
            "Stopped" if interrupted else "LM Studio  ●"
        )

        self.query_one(
            "#status",
            Static,
        ).set_classes(
            "connected"
        )

        self.query_one(
            "#prompt",
            Input,
        ).focus()

        if self.pending_messages:
            self.run_worker(
                self.start_next_queued_message(),
                exclusive=False,
            )

    async def start_next_queued_message(self):
        if self.generating or not self.pending_messages:
            return

        next_message = self.pending_messages.pop(0)
        await self.start_generation(next_message)

    def generation_error(
        self,
        message_id,
        error,
    ):
        try:
            message = self.query_one(
                f"#{message_id}",
                ChatMessage,
            )

            message.update_content(
                f"**Request failed**\n\n`{error}`"
            )

        except Exception:
            pass

        self.generating = False
        self.active_response = None

        self.query_one(
            "#status",
            Static,
        ).update(
            "Request failed"
        )

        self.query_one(
            "#prompt",
            Input,
        ).focus()

        if self.pending_messages:
            self.run_worker(
                self.start_next_queued_message(),
                exclusive=False,
            )


def main():
    GlimApp().run()
