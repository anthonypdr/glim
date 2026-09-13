# Glim

Glim is a lightweight terminal client for the models you already run locally. It starts with [LM Studio](https://lmstudio.ai/) and can connect to any local server with OpenAI-compatible model and chat endpoints.

It provides a compact terminal experience without a large, always-on system prompt. Optional project tools are only attached when you explicitly ask for them.

> Glim is early-stage software. Review agent-proposed changes and approve commands deliberately.

## Features

- Connects to LM Studio by default (`http://127.0.0.1:1234`)
- Shows the models currently running in LM Studio with `/model`
- Gives in-terminal connection steps when no model server or model is found
- Connects to other local OpenAI-compatible model servers with one environment variable
- Streams normal chat responses in a focused terminal interface
- Enables agent tools only for requests prefixed with `@`
- Lets the agent inspect and edit files inside the current project
- Requires approval for shell commands other than a small read-only allowlist
- Supports web search and readable web-page fetching in agent mode

## Install

### From a Git checkout

```bash
git clone https://github.com/anthonypdr/glim.git
cd glim
python -m pip install .
```

### Development install

Use this while working on Glim itself:

```bash
python -m pip install -e .
```

## Quick start

1. Open LM Studio and load a chat-capable model.
2. Open LM Studio's **Developer** tab and turn on **Start server**. You can also run `lms server start`.
3. Change into the project you want help with.
4. Run:

```bash
glim
```

Type `/model` to display the models currently running in LM Studio and select one. If nothing is displayed, Glim explains how to load a model and start the local server directly in the terminal.

To connect to a different local server that provides OpenAI-compatible `/v1/models` and `/v1/chat/completions` endpoints, set its base URL before launching:

```bash
GLIM_SERVER_URL=http://127.0.0.1:PORT/v1 GLIM_SERVER_NAME="My local server" glim
```

`GLIM_LMSTUDIO_URL` remains supported for an LM Studio URL override.

## Using Glim

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
| `/model` | Display running LM Studio models, or selectable models from another compatible local server |
| `/status` | Show connection, model, directory, and context status |
| `/tools` | Show the tools available through `@` agent mode |
| `/clear` | Clear normal-chat history |
| `/exit` | Quit Glim |

## How it stays lightweight

Normal chat sends only your conversation to the local model server. The optional tool instructions and definitions are attached only for requests beginning with `@`; they are not permanently included in every request. That keeps the common path small while retaining project automation when you need it.

## Development

Run a syntax check from the repository root:

```bash
python -m py_compile *.py
```

The package's command-line entry point is `glim.cli:main`. Launching `python -m glim` runs the same terminal application.

## License

MIT. See [LICENSE](LICENSE).
