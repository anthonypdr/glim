import json
import os
import requests


class LMStudio:
    def __init__(self, base_url=None):
        # This keeps the default compatible with LM Studio while making a
        # remote/containerized server an explicit user choice.
        base_url = base_url or os.environ.get(
            "GLIM_LMSTUDIO_URL",
            "http://127.0.0.1:1234",
        )
        self.base_url = base_url.rstrip("/")

    @property
    def models_url(self):
        return f"{self.base_url}/api/v1/models"

    @property
    def chat_url(self):
        return f"{self.base_url}/v1/chat/completions"

    def get_models(self):
        response = requests.get(
            self.models_url,
            timeout=5,
        )

        response.raise_for_status()

        data = response.json()

        models = []

        for item in data.get("models", []):
            if item.get("type") != "llm":
                continue

            loaded_instances = (
                item.get("loaded_instances")
                or []
            )

            models.append(
                {
                    "id": item.get("key"),
                    "name": (
                        item.get("display_name")
                        or item.get("key")
                    ),
                    "loaded": bool(
                        loaded_instances
                    ),
                    "params": item.get(
                        "params_string"
                    ),
                    "architecture": item.get(
                        "architecture"
                    ),
                }
            )

        return models

    def get_loaded_models(self):
        return [
            model
            for model in self.get_models()
            if model["loaded"]
        ]

    def stream_chat(
        self,
        model,
        messages,
        tools=None,
        tool_choice=None,
        cancel_event=None,
        on_response=None,
    ):
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools

        if tool_choice:
            payload["tool_choice"] = tool_choice

        response = requests.post(
            self.chat_url,
            headers={
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=300,
        )

        response.raise_for_status()

        if on_response:
            on_response(response)

        try:
            for line in response.iter_lines():
                if cancel_event and cancel_event.is_set():
                    break

                if not line:
                    continue

                decoded = line.decode("utf-8", errors="ignore")

                if not decoded.startswith("data: "):
                    continue

                raw = decoded[6:]
                if raw == "[DONE]":
                    break

                try:
                    yield json.loads(raw)

                except json.JSONDecodeError:
                    continue
        finally:
            response.close()

    def chat(
        self,
        model,
        messages,
        tools=None,
        tool_choice=None,
    ):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        if tools:
            payload["tools"] = tools

        if tool_choice:
            payload["tool_choice"] = tool_choice

        response = requests.post(
            self.chat_url,
            headers={
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )

        response.raise_for_status()

        return response.json()
