from __future__ import annotations

import json
import os
import re
from typing import Any


def parse_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON de una respuesta del modelo."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"Sin JSON en la respuesta: {text[:200]!r}")
    return json.loads(m.group(0))


class LLM:
    """Envoltorio fino sobre la API de Anthropic. Sin API key => available=False
    y los agentes usan su heurística determinista (modo offline)."""

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.client = None
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if key:
            import anthropic

            self.client = anthropic.Anthropic(api_key=key)

    @property
    def available(self) -> bool:
        return self.client is not None

    def complete_json(self, system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return parse_json(text)
