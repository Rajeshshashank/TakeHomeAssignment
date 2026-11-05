"""A small, dependency-free LLM client with token/cost accounting.

Supports Google Gemini (free tier), any OpenAI-compatible endpoint, and an
offline :class:`MockLLM` so the whole repo — including the measurements —
reproduces with no API key. Every call records tokens, latency and an
estimated cost so the Task 4 cost-per-1,000-requests figure is measured, not
guessed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# USD per 1M tokens. Gemini 2.0 Flash free tier is $0; the paid prices are
# listed so the cost model can be pointed at a paid key without code changes.
PRICING = {
    "gemini-2.0-flash": {"in": 0.10, "out": 0.40},
    "gemini-1.5-flash": {"in": 0.075, "out": 0.30},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "mock": {"in": 0.0, "out": 0.0},
}


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False

    @property
    def cost_usd(self) -> float:
        p = PRICING.get(self.model, PRICING["mock"])
        return (self.prompt_tokens * p["in"] + self.completion_tokens * p["out"]) / 1_000_000


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    cache_hits: int = 0

    def add(self, r: LLMResponse) -> None:
        self.calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.latency_s += r.latency_s
        self.cost_usd += r.cost_usd
        self.cache_hits += int(r.cached)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_s": round(self.latency_s, 4),
            "cost_usd": round(self.cost_usd, 6),
            "cache_hits": self.cache_hits,
        }


class LLMError(RuntimeError):
    pass


class BaseLLM:
    name = "base"

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 2048) -> LLMResponse:
        raise NotImplementedError


class MockLLM(BaseLLM):
    """Deterministic offline stand-in.

    ``responder`` maps a prompt to text. The default returns a JSON envelope so
    the LLM-compiled and parser pipelines still exercise their real code paths
    offline. Tests inject a real responder.
    """

    name = "mock"

    def __init__(self, responder: Optional[Callable[[str], str]] = None):
        self.responder = responder or (lambda p: json.dumps({"ok": False, "reason": "mock"}))

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 2048) -> LLMResponse:
        t0 = time.perf_counter()
        text = self.responder(prompt)
        return LLMResponse(
            text=text,
            model="mock",
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            latency_s=time.perf_counter() - t0,
        )


class GeminiLLM(BaseLLM):
    """Google Gemini ``generateContent`` (free tier friendly)."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", max_retries: int = 4):
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 2048) -> LLMResponse:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        data = json.dumps(body).encode()
        last: Optional[Exception] = None
        for attempt in range(self.max_retries):
            t0 = time.perf_counter()
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.load(resp)
                latency = time.perf_counter() - t0
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
                usage = payload.get("usageMetadata", {})
                return LLMResponse(
                    text=text,
                    model=self.model,
                    prompt_tokens=usage.get("promptTokenCount", 0),
                    completion_tokens=usage.get("candidatesTokenCount", 0),
                    latency_s=latency,
                )
            except urllib.error.HTTPError as exc:
                last = exc
                # 429 = rate limited, 5xx = transient.
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 20))
                    continue
                raise LLMError(f"gemini HTTP {exc.code}: {exc.read()[:300]!r}") from exc
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 20))
                    continue
        raise LLMError(f"gemini call failed: {last}")


class OpenAICompatLLM(BaseLLM):
    """Any OpenAI-compatible ``/chat/completions`` endpoint."""

    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 2048) -> LLMResponse:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        body = json.dumps({"model": self.model, "messages": msgs,
                           "temperature": temperature, "max_tokens": max_tokens}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
        latency = time.perf_counter() - t0
        usage = payload.get("usage", {})
        return LLMResponse(
            text=payload["choices"][0]["message"]["content"],
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_s=latency,
        )


@dataclass
class CachingLLM(BaseLLM):
    """Wraps another LLM and memoises by prompt+system+params."""

    inner: BaseLLM
    _cache: dict[str, LLMResponse] = field(default_factory=dict)

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"cached({self.inner.name})"

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 2048) -> LLMResponse:
        key = json.dumps([prompt, system, temperature, max_tokens])
        if key in self._cache:
            r = self._cache[key]
            return LLMResponse(r.text, r.model, r.prompt_tokens, r.completion_tokens, 0.0, True)
        r = self.inner.complete(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
        self._cache[key] = r
        return r


def get_default_llm() -> BaseLLM:
    """Pick a live provider from the environment, else fall back to MockLLM.

    ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` selects Gemini (the assignment's
    free tier); ``OPENAI_API_KEY`` selects an OpenAI-compatible endpoint.
    """
    gem = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gem:
        return GeminiLLM(gem, model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    oai = os.environ.get("OPENAI_API_KEY")
    if oai:
        return OpenAICompatLLM(oai, os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                               model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    return MockLLM()
