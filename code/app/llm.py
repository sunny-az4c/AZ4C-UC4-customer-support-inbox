"""Model calls with JSON output, validation, retries, back-off and a provider fallback chain.

Providers are OpenAI-compatible chat endpoints:
  Groq:   GROQ_BASE_URL   + /openai/v1/chat/completions
  Gemini: GEMINI_BASE_URL + /v1beta/openai/chat/completions
A provider is used only when its key is set. If every provider fails, LLMError is raised and the
calling agent falls back to its rule-based path, so a run never depends on a model being up.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Callable

import httpx

from . import config
from .trace import Trace

PROMPT_VERSION = "v3"


class LLMError(Exception):
    pass


class InvalidReply(Exception):
    pass


def _wait_from_headers(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying, from retry-after or Groq's x-ratelimit-reset-* headers."""
    for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = resp.headers.get(h)
        if not v:
            continue
        try:
            return float(v)
        except ValueError:
            m = re.fullmatch(r"(?:(\d+)m)?([\d.]+)s", v.strip())
            if m:
                return int(m.group(1) or 0) * 60 + float(m.group(2))
    return 2.0 * (2 ** attempt)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise InvalidReply("reply is not JSON")
        try:
            out = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise InvalidReply(f"reply is not valid JSON: {e}") from e
    if not isinstance(out, dict):
        raise InvalidReply("reply JSON is not an object")
    return out


class LLM:
    def __init__(self, trace: Trace):
        self.trace = trace
        self.client = httpx.Client(timeout=config.LLM_TIMEOUT)
        self.models_used: set[str] = set()

    def available(self) -> bool:
        return not config.LLM_OFFLINE and bool(config.GROQ_API_KEY or config.GEMINI_API_KEY)

    def _chain(self, groq_model: str) -> list[tuple[str, str]]:
        chain = []
        if config.GROQ_API_KEY and config.GROQ_BASE_URL:
            chain.append(("groq", groq_model))
            backup = config.WRITER_MODEL if groq_model != config.WRITER_MODEL else config.TRIAGE_MODEL
            chain.append(("groq", backup))
        if config.GEMINI_API_KEY and config.GEMINI_BASE_URL:
            chain.append(("gemini", config.GEMINI_MODEL))
        return chain

    def _endpoint(self, provider: str) -> tuple[str, str]:
        if provider == "groq":
            return config.GROQ_BASE_URL + "/openai/v1/chat/completions", config.GROQ_API_KEY
        return config.GEMINI_BASE_URL + "/v1beta/openai/chat/completions", config.GEMINI_API_KEY

    def json_call(self, agent: str, model: str, system: str, user: str,
                  validate: Callable[[dict], dict], max_tokens: int = 1500) -> dict:
        """Call a model for a JSON object, validated by `validate` (which may raise InvalidReply)."""
        if not self.available():
            raise LLMError("no model provider configured (or LLM_OFFLINE=1)")
        errors = []
        for provider, mdl in self._chain(model):
            key = hashlib.sha256(json.dumps([PROMPT_VERSION, provider, mdl, system, user]).encode()).hexdigest()[:32]
            cache_file = config.CACHE_DIR / f"{key}.json"
            if config.LLM_CACHE and cache_file.is_file():
                try:
                    out = validate(json.loads(cache_file.read_text(encoding="utf-8")))
                    self.trace.log(agent, "llm_call", provider=provider, model=mdl, cached=True)
                    self.models_used.add(f"{provider}:{mdl}")
                    return out
                except (InvalidReply, json.JSONDecodeError):
                    pass
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            for attempt in range(4):
                try:
                    raw = self._post(provider, mdl, messages, max_tokens, agent, attempt)
                    parsed = _parse_json(raw)
                    out = validate(parsed)
                    if config.LLM_CACHE:
                        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        cache_file.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
                    self.models_used.add(f"{provider}:{mdl}")
                    return out
                except InvalidReply as e:
                    # Bad reply: tell the model what was wrong and try once more on this model.
                    self.trace.log(agent, "llm_invalid_reply", provider=provider, model=mdl, error=str(e))
                    errors.append(f"{mdl}: invalid reply: {e}")
                    if attempt >= 1:
                        break
                    messages = messages[:2] + [{"role": "user", "content":
                        f"Your previous reply was rejected: {e}. Reply again with only the corrected JSON object."}]
                except _Retry as e:
                    errors.append(f"{mdl}: {e}")
                    if e.wait is None or e.wait > config.LLM_MAX_WAIT:
                        break
                    time.sleep(e.wait)
                except _Fatal as e:
                    errors.append(f"{mdl}: {e}")
                    break
        raise LLMError("; ".join(errors[-4:]) or "all providers failed")

    def _post(self, provider: str, model: str, messages: list, max_tokens: int, agent: str, attempt: int) -> str:
        url, key = self._endpoint(provider)
        body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}}
        if provider == "groq" and model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
        t = time.perf_counter()
        try:
            resp = self.client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            self.trace.log(agent, "llm_error", provider=provider, model=model, attempt=attempt, error=type(e).__name__)
            raise _Retry(f"network error {type(e).__name__}", 2.0 * (2 ** attempt))
        ms = round((time.perf_counter() - t) * 1000)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = _wait_from_headers(resp, attempt) + 0.5
            self.trace.log(agent, "llm_retry", provider=provider, model=model, status=resp.status_code,
                           attempt=attempt, wait_s=round(wait, 1))
            raise _Retry(f"HTTP {resp.status_code}", wait)
        if resp.status_code != 200:
            msg = resp.text[:200]
            self.trace.log(agent, "llm_error", provider=provider, model=model, status=resp.status_code, error=msg)
            # A json_validate_failed 400 from Groq is a bad model reply, not a bad request: retry it.
            if resp.status_code == 400 and "json_validate_failed" in msg and attempt < 1:
                raise _Retry("model produced invalid JSON", 0.5)
            raise _Fatal(f"HTTP {resp.status_code}")
        data = resp.json()
        usage = data.get("usage", {})
        self.trace.log(agent, "llm_call", provider=provider, model=model, ms=ms, attempt=attempt,
                       prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise InvalidReply("no message content in response")


class _Retry(Exception):
    def __init__(self, msg: str, wait: float | None):
        super().__init__(msg)
        self.wait = wait


class _Fatal(Exception):
    pass
