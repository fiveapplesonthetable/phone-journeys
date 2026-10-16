"""Small, fail-closed client for the Jev decision API."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping


TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
VENICE_URL = "https://api.venice.ai/api/v1/decisions"
MODEL = "jev-latest"
DEFAULT_CONFIG = Path.home() / ".config/phone-journeys/jev.env"


class JevError(Exception):
    """Base class for safe-to-display Jev client errors."""


class JevConfigurationError(JevError):
    pass


class JevRequestError(JevError):
    pass


class JevResponseError(JevError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from forwarding the bearer token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_env_file(path: Path) -> dict[str, str]:
    """Read KEY=value lines as data; never evaluate shell syntax."""
    values: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    except (OSError, UnicodeError):
        raise JevConfigurationError("Could not read Jev configuration file") from None

    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


class JevClient:
    """Call Jev using a fixed provider endpoint and validate every decision.

    ``provider`` is ``"typesafe"`` or ``"venice"``. The corresponding key
    comes from ``TYPESAFE_API_KEY`` or ``VENICE_API_KEY`` in the process
    environment first, then ``~/.config/phone-journeys/jev.env``.
    """

    def __init__(
        self,
        provider: str = "typesafe",
        *,
        api_key: str | None = None,
        config_path: str | os.PathLike[str] | None = None,
        opener: Any = None,
        sleeper: Callable[[float], None] = time.sleep,
        timeout: float = 20.0,
    ) -> None:
        provider = provider.lower()
        if provider not in ("typesafe", "venice"):
            raise JevConfigurationError("Provider must be 'typesafe' or 'venice'")
        self.provider = provider
        self.url = TYPESAFE_URL if provider == "typesafe" else VENICE_URL
        self.model = MODEL
        key_name = "TYPESAFE_API_KEY" if provider == "typesafe" else "VENICE_API_KEY"
        env_values = _read_env_file(Path(config_path) if config_path else DEFAULT_CONFIG)
        # An explicitly supplied key is useful for embedding and is treated like
        # an environment override; the process environment always wins.
        self.api_key = os.environ.get(key_name) or api_key or env_values.get(key_name)
        if not self.api_key:
            raise JevConfigurationError(
                f"Missing {key_name}; set it in the environment or Jev config file"
            )
        self._opener = (
            opener
            if opener is not None
            else urllib.request.build_opener(_NoRedirectHandler()).open
        )
        self._sleep = sleeper
        self.timeout = timeout

    def choose(
        self,
        state: Mapping[str, Any],
        criteria: Mapping[str, str],
        instructions: str,
    ) -> dict[str, Any]:
        """Ask Jev to choose one action and return a validated result."""
        if not isinstance(state, Mapping):
            raise ValueError("state must be an object")
        if not isinstance(criteria, Mapping) or not criteria:
            raise ValueError("criteria must be a non-empty mapping")
        if not all(isinstance(k, str) and k for k in criteria):
            raise ValueError("criteria keys must be non-empty strings")
        if not all(isinstance(v, str) and v for v in criteria.values()):
            raise ValueError("criteria descriptions must be non-empty strings")
        if not isinstance(instructions, str) or not instructions:
            raise ValueError("instructions must be a non-empty string")

        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                "next_action": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": dict(criteria),
                }
            },
        }
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        raw = self._post(req)
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise JevResponseError("Jev returned invalid JSON") from None
        return self._validate_response(response, set(criteria), latency_ms)

    def _post(self, request: urllib.request.Request) -> bytes:
        for attempt in range(3):
            try:
                response = self._opener(request, timeout=self.timeout)
                try:
                    return response.read()
                finally:
                    close = getattr(response, "close", None)
                    if close:
                        close()
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    exc.close()
                except Exception:
                    pass
                if status == 429 or 500 <= status <= 599:
                    if attempt < 2:
                        self._sleep(min(0.5 * (2**attempt), 2.0))
                        continue
                raise JevRequestError(f"Jev request failed with HTTP {status}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                # Transport errors may contain URLs or low-level details. Avoid
                # exposing them because implementations can include headers.
                raise JevRequestError("Jev request failed due to a transport error") from None
        raise JevRequestError("Jev request failed")

    @staticmethod
    def _validate_response(
        response: Any, option_ids: set[str], latency_ms: int
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise JevResponseError("Jev response must be a JSON object")
        model = response.get("model")
        if not isinstance(model, str) or not model.startswith("jev-") or len(model) <= 4:
            raise JevResponseError("Jev response named an unexpected model")
        answers = response.get("answers")
        answer = answers.get("next_action") if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise JevResponseError("Jev response is missing the next_action choice")
        choice = answer.get("choice")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(choice, str) or choice not in option_ids:
            raise JevResponseError("Jev selected an unknown action")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise JevResponseError("Jev returned an invalid confidence")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise JevResponseError("Jev returned an invalid confidence")
        if not isinstance(probabilities, dict) or set(probabilities) != option_ids:
            raise JevResponseError("Jev returned an incomplete action probability map")
        checked: dict[str, float] = {}
        for key, value in probabilities.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JevResponseError("Jev returned invalid action probabilities")
            number = float(value)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise JevResponseError("Jev returned invalid action probabilities")
            checked[key] = number
        if not math.isclose(sum(checked.values()), 1.0, rel_tol=0, abs_tol=0.01):
            raise JevResponseError("Jev action probabilities do not sum to one")
        return {
            "choice": choice,
            "confidence": confidence,
            "probabilities": checked,
            "model": model,
            "usage": response.get("usage"),
            "latency_ms": latency_ms,
        }
