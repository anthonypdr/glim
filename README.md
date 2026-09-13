# LocalAI

LocalAI is a lightweight, local-first terminal coding assistant for [LM Studio](https://lmstudio.ai/). It is designed for people who want an agent that works in their project without a large, always-on instruction prompt slowing down a local model.

It is its own terminal experience: compact chat for everyday questions, with project and web tools enabled only when you explicitly ask for agent work.

> LocalAI is early-stage software. Review agent-proposed changes and approve commands deliberately.

## Features

- Connects to the LM Studio local server (default: `http://127.0.0.1:1234`)
- Streams normal chat responses in a focused terminal interface
- Enables agent tools only for requests prefixed with `@`
- Lets the agent inspect and edit files inside the current project
- Requires approval for shell commands other than a small read-only allowlist
- Supports web search and readable web-page fetching in agent mode

## Install

### From a Git checkout

```bash
git clone https://github.com/anthonypdr/LocalAI.git
cd LocalAI
python -m pip install .
```

### Development install

Use this while working on LocalAI itself:

```bash
python -m pip install -e .
```

## Quick start

1. Open LM Studio and load a chat-capable model.
2. Start LM Studio's local server on port `1234`.
3. Change into the project you want help with.
4. Run:

```bash
localai
```

LocalAI displays the loaded model and working directory at startup. It reads and writes only within the directory from which you launch it.

To connect to a server at a different address, set `LOCALAI_LMSTUDIO_URL` before launching:

```bash
LOCALAI_LMSTUDIO_URL=http://192.168.1.10:1234 localai
```

## Using LocalAI

Type a normal message for lightweight chat:

```text
Explain this Python traceback.
```

Prefix a request with `@` to enable the agent and its tools for that request:

```text
@ inspect this project and explain its structure
@ read package.json and summarize the dependencies
@ fix the failing test in tests/test_api.py
@ run pytest
@ search the web for the current Python packaging guidance
```

`@ run COMMAND` directly runs a shell command through the same safety checks:

```text
@ run rg "TODO" .
```

Safe read-only commands run immediately. Commands that can alter the system or project require an explicit `y` confirmation. Highly destructive commands are blocked.

### Built-in commands

| Command | What it does |
| --- | --- |
| `/help` | Show in-app help |
| `/model` | List loaded models and choose one |
| `/status` | Show connection, model, directory, and context status |
| `/tools` | Show the tools available through `@` agent mode |
| `/clear` | Clear normal-chat history |
| `/exit` | Quit LocalAI |

## How it stays lightweight

Normal chat sends only your conversation to LM Studio. The coding-agent instructions and tool definitions are attached only for requests beginning with `@`; they are not permanently included in every chat request. That keeps the common path small while retaining project automation when you need it.

## Development

Run a syntax check from the repository root:

```bash
python -m py_compile *.py
```

The package's command-line entry point is `localai.cli:main`. Launching `python -m localai` runs the same terminal application.

## License

MIT. See [LICENSE](LICENSE).
