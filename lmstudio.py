import json
import os
import requests
from glim.conversation import estimate_tokens


class LMStudio:
    def __init__(self, base_url=None):
        # LM Studio is the zero-configuration default. Any server that
        # implements OpenAI's /v1/models and /v1/chat/completions endpoints
        # can be selected explicitly without introducing a provider framework.
        base_url = (
            base_url
            or os.environ.get("GLIM_SERVER_URL")
            or os.environ.get("GLIM_LMSTUDIO_URL")
            or "http://127.0.0.1:1234"
        )
        self.base_url = base_url.rstrip("/")
        # Accept both server roots and the /v1 base URLs used by compatible SDKs.
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.server_name = os.environ.get(
            "GLIM_SERVER_NAME",
            "LM Studio",
        )
        self.has_loaded_state = False
        self.context_usage = None

    def _begin_usage(self, model, messages, tools):
        self.context_usage = {"model": model, "tokens": estimate_tokens(messages, tools), "estimated": True}

    def _record_usage(self, data):
        usage = data.get("usage") or {}
        tokens = usage.get("total_tokens")
        if not isinstance(tokens, int):
            prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if isinstance(prompt, int) and isinstance(completion, int):
                tokens = prompt + completion
        if isinstance(tokens, int) and tokens >= 0 and self.context_usage:
            self.context_usage = {**self.context_usage, "tokens": tokens, "estimated": False}

    @property
    def models_url(self):
        return f"{self.base_url}/api/v1/models"

    @property
    def compatible_models_url(self):
        return f"{self.base_url}/v1/models"

    @property
    def chat_url(self):
        return f"{self.base_url}/v1/chat/completions"

    def get_models(self):
        """Return models from LM Studio or an OpenAI-compatible local server.

        LM Studio's native endpoint exposes whether an installed LLM is
        loaded, so Glim can show exactly what is running. Other compatible
        servers expose only the models they make available; those are still
        usable selections but are labelled accordingly in the terminal.
        """
        try:
            response = requests.get(
                self.models_url,
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as native_error:
            try:
                response = requests.get(
                    self.compatible_models_url,
                    timeout=5,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as compatible_error:
                raise ConnectionError(
                    f"Could not reach {self.server_name} at "
                    f"{self.base_url}."
                ) from compatible_error

            self.has_loaded_state = False
            return self._compatible_models(data)

        self.has_loaded_state = True
        return self._lmstudio_models(data)

    @staticmethod
    def _lmstudio_models(data):

        models = []

        for item in data.get("models", []):
            if item.get("type") != "llm":
                continue

            loaded_instances = (
                item.get("loaded_instances")
                or []
            )
            # Use the loaded window, not the model's theoretical maximum.
            lengths = {(instance.get("config") or {}).get("context_length")
                       for instance in loaded_instances}
            context_length = lengths.pop() if len(lengths) == 1 else None

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
                    "context_length": context_length,
                    "params": item.get(
                        "params_string"
                    ),
                    "architecture": item.get(
                        "architecture"
                    ),
                }
            )

        return models

    @staticmethod
    def _compatible_models(data):
        models = []

        for item in data.get("data", []):
            if isinstance(item, str):
                item = {"id": item}

            model_id = item.get("id")
            if not model_id:
                continue

            models.append(
                {
                    "id": model_id,
                    "name": item.get("name") or model_id,
                    # Compatible servers generally do not expose a separate
                    # loaded-state field. Their advertised models are usable.
                    "loaded": True,
                    "params": None,
                    "architecture": None,
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
        self._begin_usage(model, messages, tools)
        prompt_estimate = self.context_usage["tokens"]
        output_chars = 0
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if self.has_loaded_state:
            payload["stream_options"] = {"include_usage": True}

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
                    chunk = json.loads(raw)
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        output_chars += len(delta.get("content") or "")
                        output_chars += len(delta.get("reasoning_content") or "")
                    if self.context_usage["estimated"]:
                        self.context_usage = {**self.context_usage, "tokens": prompt_estimate + (output_chars + 3) // 4}
                    self._record_usage(chunk)
                    # Usage-only SSE events have no choices; consumers render
                    # content, while the footer reads the usage snapshot.
                    if chunk.get("choices"):
                        yield chunk

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
        self._begin_usage(model, messages, tools)
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

        data = response.json()
        output = [choice.get("message") or {} for choice in data.get("choices") or []]
        self.context_usage = {**self.context_usage,
                              "tokens": self.context_usage["tokens"] + estimate_tokens(output)}
        self._record_usage(data)
        return data
