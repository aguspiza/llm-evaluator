import httpx
from typing import List, Dict, Optional


class OpenAIClient:
    """Unified OpenAI-compatible API client."""

    def __init__(
        self, base_url: str, api_key: Optional[str] = None, timeout: float = 600.0
    ):
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3].rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if api_key and api_key != "not-needed":
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(
            base_url=self.base_url, headers=self.headers, timeout=timeout
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "local",
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> tuple[str, dict]:
        """Send a chat completion request and return (content, usage)."""
        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens > 0:
            data["max_tokens"] = max_tokens

        response = self.client.post("/v1/chat/completions", json=data)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return content, usage

    def tokenize(self, text: str) -> list:
        response = self.client.post("/tokenize", json={"content": text})
        response.raise_for_status()
        return response.json()["tokens"]

    def detokenize(self, tokens: list) -> str:
        response = self.client.post("/detokenize", json={"tokens": tokens})
        response.raise_for_status()
        return response.json()["content"]

    def benchmark(self, prompt: str, n_predict: int = 0) -> dict:
        data = {"prompt": prompt, "n_predict": n_predict, "cache_prompt": False}
        response = self.client.post("/completion", json=data, timeout=3600)
        response.raise_for_status()
        return response.json().get("timings", {})

    def health_check(self) -> bool:
        """Check if the server is ready."""
        try:
            response = self.client.get("/health")
            if response.status_code != 200:
                return False
            data = response.json()
            return data.get("status", "ok") == "ok"
        except Exception:
            return False

    def close(self):
        self.client.close()
