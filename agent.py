import json

from glim.tools import (
    list_files,
    read_file,
    write_file,
    run_command,
    execute_command,
)

from glim.web_tools import (
    fetch_url,
    web_search,
)


AGENT_SYSTEM_PROMPT = """
You are a coding assistant operating inside the user's current project.

Use tools whenever the user's request requires interacting with files,
running commands, or accessing the internet.

Available capabilities:
- list project files
- read project files
- create or modify project files
- run shell commands
- search the web
- fetch web pages

Important rules:
- If the user asks you to inspect a file, use a file tool.
- If the user asks you to run, execute, test, install, build, or check
  something using the terminal, use run_command.
- If the user asks for current information from the internet, use web tools.
- Do not claim you inspected something unless you actually used a tool.
- Read relevant files before modifying them.
- Shell commands may require explicit approval from the user.
- If a command is declined or blocked, do not bypass the restriction.
- Keep tool usage focused and minimal.
""".strip()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories in the current project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "default": ".",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file in the current project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file in the current project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "content": {
                        "type": "string",
                    },
                },
                "required": [
                    "path",
                    "content",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command in the current project. "
                "Use this whenever the user asks to run, execute, test, "
                "build, install, or check something in the terminal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public internet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch readable text from a public web page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                    }
                },
                "required": ["url"],
            },
        },
    },
]


TOOL_FUNCTIONS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "run_command": run_command,
    "web_search": web_search,
    "fetch_url": fetch_url,
}


class Agent:
    def __init__(
        self,
        lmstudio,
        model,
        console,
        confirm_callback=None,
    ):
        self.lmstudio = lmstudio
        self.model = model
        self.console = console
        self.confirm_callback = confirm_callback

    def run(self, task, max_steps=12):
        # -------------------------------------------------
        # DIRECT COMMAND MODE
        #
        # @ run <command>
        #
        # This does NOT ask the LLM whether it wants to use
        # run_command. The CLI knows that "run" means shell.
        # -------------------------------------------------

        stripped_task = task.strip()

        if stripped_task.lower().startswith("run "):
            command = stripped_task[4:].strip()

            if not command:
                return "No command was provided."

            return self._run_direct_command(command)

        # -------------------------------------------------
        # NORMAL AGENT MODE
        # -------------------------------------------------

        messages = [
            {
                "role": "system",
                "content": AGENT_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": task,
            },
        ]

        for _ in range(max_steps):
            response = self.lmstudio.chat(
                self.model,
                messages,
                tools=TOOLS,
                tool_choice="auto",
            )

            message = (
                response
                .get("choices", [{}])[0]
                .get("message", {})
            )

            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                return message.get("content") or "(No response)"

            messages.append(message)

            for tool_call in tool_calls:
                function = tool_call.get(
                    "function",
                    {},
                )

                name = function.get("name")

                raw_arguments = function.get(
                    "arguments",
                    "{}",
                )

                try:
                    arguments = json.loads(
                        raw_arguments
                    )
                except json.JSONDecodeError:
                    arguments = {}

                self._print_tool_start(
                    name,
                    arguments,
                )

                result = self._execute_tool(
                    name,
                    arguments,
                )

                self._print_tool_result(
                    name,
                    result,
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": json.dumps(
                            self._result_for_model(result),
                            ensure_ascii=False,
                        ),
                    }
                )

        return (
            "Agent stopped because it reached "
            "the maximum number of tool steps."
        )

    @staticmethod
    def _result_for_model(result):
        """Do not feed a human-facing diff back into the model context."""
        model_result = result.copy()
        model_result.pop("diff", None)
        return model_result

    def _run_direct_command(self, command):
        self._print_tool_start(
            "run_command",
            {
                "command": command,
            },
        )

        result = self._execute_tool(
            "run_command",
            {
                "command": command,
            },
        )

        self._print_tool_result(
            "run_command",
            result,
        )

        if result.get("cancelled"):
            return "Command cancelled."

        if result.get("blocked"):
            return (
                "The command was blocked because it is considered "
                "highly destructive."
            )

        if not result.get("ok"):
            error = result.get(
                "error",
                "Command failed.",
            )

            stderr = result.get(
                "stderr",
                "",
            )

            if stderr:
                return f"{error}\n\n{stderr}"

            return error

        stdout = result.get(
            "stdout",
            "",
        ).strip()

        stderr = result.get(
            "stderr",
            "",
        ).strip()

        output = []

        if stdout:
            output.append(stdout)

        if stderr:
            output.append(stderr)

        if not output:
            return (
                f"Command completed successfully "
                f"with exit code {result.get('returncode', 0)}."
            )

        return "\n\n".join(output)

    def _execute_tool(
        self,
        name,
        arguments,
    ):
        function = TOOL_FUNCTIONS.get(name)

        if not function:
            return {
                "ok": False,
                "error": f"Unknown tool: {name}",
            }

        try:
            result = function(
                **arguments
            )

        except Exception as error:
            return {
                "ok": False,
                "error": str(error),
            }

        if (
            name == "run_command"
            and result.get("requires_confirmation")
        ):
            command = result.get(
                "command",
                "",
            )

            approved = False

            if self.confirm_callback:
                approved = self.confirm_callback(
                    command
                )

            if not approved:
                return {
                    "ok": False,
                    "cancelled": True,
                    "command": command,
                    "error": "User declined the command.",
                }

            return execute_command(
                command
            )

        return result

    def _print_tool_start(
        self,
        name,
        arguments,
    ):
        if name == "read_file":
            self.console.print(
                f"[dim]• Reading "
                f"{arguments.get('path', '')}[/dim]"
            )

        elif name == "write_file":
            self.console.print(
                f"[dim]• Editing "
                f"{arguments.get('path', '')}[/dim]"
            )

        elif name == "list_files":
            self.console.print(
                f"[dim]• Listing "
                f"{arguments.get('path', '.')}[/dim]"
            )

        elif name == "run_command":
            self.console.print(
                f"[dim]• Running "
                f"{arguments.get('command', '')}[/dim]"
            )

        elif name == "web_search":
            self.console.print(
                f"[dim]• Searching web for "
                f"{arguments.get('query', '')}[/dim]"
            )

        elif name == "fetch_url":
            self.console.print(
                f"[dim]• Fetching "
                f"{arguments.get('url', '')}[/dim]"
            )

        else:
            self.console.print(
                f"[dim]• {name}[/dim]"
            )

    def _print_tool_result(
        self,
        name,
        result,
    ):
        if result.get("blocked"):
            self.console.print(
                "[red]  blocked[/red]"
            )
            return

        if result.get("cancelled"):
            self.console.print(
                "[yellow]  cancelled[/yellow]"
            )
            return

        if not result.get("ok"):
            error = result.get(
                "error",
                "Unknown error",
            )

            self.console.print(
                f"[red]  failed: {error}[/red]"
            )
            return

        if name == "write_file":
            action = result.get(
                "action",
                "updated",
            )

            path = result.get(
                "path",
                "",
            )

            self.console.print(
                f"[green]  {action} {path}[/green]"
            )

            diff = result.get("diff", "")
            if diff:
                rendered_diff = Text()
                for line in diff.splitlines():
                    style = (
                        "green" if line.startswith("+") and not line.startswith("+++")
                        else "red" if line.startswith("-") and not line.startswith("---")
                        else "dim"
                    )
                    rendered_diff.append(line + "\n", style=style)
                self.console.print(rendered_diff)

        elif name == "run_command":
            returncode = result.get(
                "returncode"
            )

            self.console.print(
                f"[green]  exited {returncode}[/green]"
            )

        elif name == "web_search":
            count = len(
                result.get(
                    "results",
                    [],
                )
            )

            self.console.print(
                f"[green]  {count} results[/green]"
            )

        else:
            self.console.print(
                "[green]  done[/green]"
            )
