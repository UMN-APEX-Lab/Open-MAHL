"""LLM client.

A single :class:`LLMClient` wraps both backends (OpenAI and Ollama), chooses the
model for each *role* from :class:`~mahl_lite.config.Config`, retries on transient
failures, and raises :class:`~mahl_lite.errors.LLMError` on hard failures (no more
``"Error: ..."`` sentinel strings).

The client also drives two optional TUI hooks if a ``tui`` is attached:
``set_active_agent`` (which agent box lights up) and ``add_memory`` (context gauge).
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .config import Config
from .errors import LLMError
from .log import Log

# How each role is labelled in the TUI's "Active Agents" panel.
ROLE_DISPLAY = {
    "planner": "Planner Agent",
    "reasoner": "Reasoner Agent",
    "coder": "Coder Agent",
    "verifier": "Verifier Agent",
}


class LLMClient:
    def __init__(self, config: Config, tui=None):
        self.config = config
        self.tui = tui
        self._openai_client = None  # lazily constructed

    # ----- public API -----
    def complete(self, prompt: str, role: str, agent_name: Optional[str] = None) -> str:
        """Run one completion as ``role``. Raises :class:`LLMError` on failure."""
        model = self.config.models.for_role(role)
        display = agent_name or ROLE_DISPLAY.get(role, role)

        if self.tui:
            # small beat so fast steps are still visible in the live UI
            time.sleep(0.15)
            _try(self.tui, "set_active_agent", display)
            _try(self.tui, "add_memory", prompt)

        try:
            result = self._complete_with_retries(prompt, model)
        finally:
            if self.tui:
                _try(self.tui, "set_active_agent", None)

        if self.tui:
            _try(self.tui, "add_memory", result)
        return result

    # ----- internals -----
    def _complete_with_retries(self, prompt: str, model: str) -> str:
        attempts = max(1, self.config.sampling.retries + 1)
        last_err: Optional[Exception] = None
        for i in range(attempts):
            try:
                return self._dispatch(prompt, model)
            except LLMError:
                raise  # configuration/usage errors should not be retried
            except Exception as e:  # transient: network, rate limit, etc.
                last_err = e
                if i < attempts - 1:
                    backoff = 0.5 * (2 ** i)
                    Log.warn(f"LLM call failed ({e}); retry {i + 1}/{attempts - 1} in {backoff:.1f}s")
                    time.sleep(backoff)
        raise LLMError(f"LLM call failed after {attempts} attempt(s): {last_err}") from last_err

    def _dispatch(self, prompt: str, model: str) -> str:
        if model == "openai":
            return self._openai_call(prompt)
        return self._ollama_call(prompt, model)

    def _openai_call(self, prompt: str) -> str:
        client = self._get_openai()
        s = self.config.sampling
        resp = client.chat.completions.create(
            model=s.openai_model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=s.temperature,
            top_p=s.top_p,
            max_tokens=s.max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def _get_openai(self):
        if self._openai_client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise LLMError("OpenAI SDK not installed. Try `pip install openai`.") from e
            if not os.getenv("OPENAI_API_KEY"):
                raise LLMError("OPENAI_API_KEY is not set (put it in .env or the environment).")
            self._openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return self._openai_client

    def _ollama_call(self, prompt: str, model: str) -> str:
        try:
            import ollama
        except ImportError as e:
            raise LLMError("Ollama not available. `pip install ollama` and run the daemon.") from e
        options = _ollama_options(model)
        r = ollama.chat(messages=[{"role": "user", "content": prompt}], model=model, options=options)
        if isinstance(r, dict):
            msg = r.get("message")
            if isinstance(msg, dict):
                return (msg.get("content") or "").strip()
            return (r.get("content") or "").strip()
        msg = getattr(r, "message", None)
        return (msg.content if msg else "").strip()


def _ollama_options(model: str) -> dict:
    if model.startswith("gemma3"):
        return {"temperature": 0.6, "top_p": 0.95, "top_k": 64}
    if model.startswith(("llama3.3", "deepseek-r1")):
        return {"temperature": 0.6}
    return {"temperature": 0.3}


def _try(obj, method: str, *args) -> None:
    """Call an optional TUI hook if present, ignoring UI errors."""
    fn = getattr(obj, method, None)
    if callable(fn):
        try:
            fn(*args)
        except Exception:
            pass
