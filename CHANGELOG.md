# Changelog

## 0.1.0a1 — First public alpha

Glim brings a lightweight terminal interface to locally hosted language models.

- Normal streaming chat without tool definitions or a built-in system prompt.
- LM Studio model discovery and support for local OpenAI-compatible servers,
  including an Ollama setup guide.
- Opt-in `@` agent requests for project files, shell commands, and web tools.
- Command approval choices, highlighted code and diffs, queued follow-ups,
  shared session history, and context usage when available.
- Server URLs accept either a server root or a trailing `/v1`.
- Illustrated README, isolated installation instructions, and automated tests
  on Linux, macOS, and Windows with Python 3.10 and 3.14.

### Alpha limitations

- Model quality and tool calling depend on the selected model and server.
- Conversation history is held in memory and is lost on restart.
- Generic compatible servers may not expose a context limit.
- File edits can happen during `@` requests without a separate approval prompt;
  command approval is not an operating-system sandbox.
- Web tools contact external services; they are not an offline feature.
