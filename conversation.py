"""Present agent observations to plain chat without enabling tool calling."""

import json


def estimate_tokens(messages, tools=None):
    """A labelled fallback estimate, not a substitute for the model tokenizer."""
    if not messages and not tools:
        return 0
    text = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
    return max(1, (len(text) + 3) // 4)


def chat_history(messages):
    history = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            observation = "Tool result (observed output):\n" + message.get("content", "")
            if history and history[-1]["role"] == "assistant":
                history[-1]["content"] += "\n\n" + observation
            else:
                history.append({"role": "assistant", "content": observation})
        elif message.get("tool_calls"):
            content = message.get("content") or ""
            for call in message["tool_calls"]:
                function = call.get("function", {})
                content += "\n\nPrevious tool call:\n" + json.dumps(function, ensure_ascii=False)
            history.append({"role": "assistant", "content": content.strip()})
        else:
            history.append({"role": role, "content": message.get("content") or ""})
    return history
