import httpx
from typing import List, Dict, Optional


class OpenAIClient:
    """Unified OpenAI-compatible API client."""

    def __init__(
        self, base_url: str, api_key: Optional[str] = None, timeout: float = 300.0
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
    ) -> str:
        """Send a chat completion request and return the full response."""
        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens > 0:
            data["max_tokens"] = max_tokens

        response = self.client.post("/v1/chat/completions", json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def health_check(self) -> bool:
        """Check if the server is ready."""
        try:
            response = self.client.get("/health")
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        self.client.close()
