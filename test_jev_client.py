import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from jev_client import (
    JevClient,
    JevConfigurationError,
    JevRequestError,
    JevResponseError,
    MODEL,
    TYPESAFE_URL,
    VENICE_URL,
)


class Response:
    def __init__(self, obj):
        self.content = json.dumps(obj).encode()

    def read(self):
        return self.content

    def close(self):
        pass


def valid_response(**overrides):
    answer = {
        "type": "choice",
        "choice": "continue",
        "confidence": 0.8,
        "probabilities": {"continue": 0.8, "stop": 0.2},
    }
    response = {"model": MODEL, "answers": {"next_action": answer}, "usage": {"total": 4}}
    response.update(overrides)
    return response


class JevClientTests(unittest.TestCase):
    def client(self, opener, **kw):
        kw.setdefault("sleeper", lambda _: None)
        return JevClient(api_key="secret-token", opener=opener, **kw)

    def test_posts_contract_and_returns_decision(self):
        captured = {}

        def opener(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = req.headers
            captured["body"] = json.loads(req.data)
            captured["timeout"] = timeout
            return Response(valid_response())

        result = self.client(opener).choose(
            {"screen": "home"}, {"continue": "Proceed", "stop": "End"}, "Choose safely"
        )
        self.assertEqual(captured["url"], TYPESAFE_URL)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(captured["timeout"], 20)
        self.assertEqual(captured["body"], {
            "model": "jev-latest",
            "state": {"screen": "home"},
            "questions": {"next_action": {
                "type": "choice", "instructions": "Choose safely",
                "criteria": {"continue": "Proceed", "stop": "End"},
            }},
        })
        self.assertEqual(result["choice"], "continue")
        self.assertEqual(result["probabilities"], {"continue": 0.8, "stop": 0.2})
        self.assertEqual(result["usage"], {"total": 4})
        self.assertGreaterEqual(result["latency_ms"], 0)

    def test_accepts_docs_resolved_model_version(self):
        # The API resolves the request model "jev-latest" to a concrete version.
        response = valid_response(model="jev-1.13.0")
        result = self.client(lambda *_a, **_kw: Response(response)).choose(
            {}, {"continue": "go", "stop": "halt"}, "choose"
        )
        self.assertEqual(result["model"], "jev-1.13.0")

    def test_default_transport_disables_redirects(self):
        captured = {}
        fake_opener = mock.Mock()

        def build_opener(*handlers):
            captured["handlers"] = handlers
            return fake_opener

        with mock.patch("jev_client.urllib.request.build_opener", side_effect=build_opener):
            client = JevClient(api_key="secret-token")
        redirect_handlers = [h for h in captured["handlers"] if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertEqual(len(redirect_handlers), 1)
        handler = redirect_handlers[0]
        request = urllib.request.Request(TYPESAFE_URL, headers={"Authorization": "Bearer secret-token"})
        self.assertIsNone(handler.redirect_request(request, None, 307, "redirect", {}, "https://attacker.invalid/"))

        fake_opener.open.side_effect = urllib.error.HTTPError(
            TYPESAFE_URL, 307, "redirect", {"Location": "https://attacker.invalid/"}, None
        )
        with self.assertRaisesRegex(JevRequestError, "HTTP 307"):
            client.choose({}, {"continue": "go", "stop": "halt"}, "choose")
        fake_opener.open.assert_called_once()

    def test_reads_config_as_data_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jev.env"
            path.write_text("TYPESAFE_API_KEY='file-token'\n", encoding="utf-8")
            client = JevClient(config_path=path, opener=lambda *_a, **_kw: Response(valid_response()))
            self.assertEqual(client.api_key, "file-token")
            old = os.environ.get("TYPESAFE_API_KEY")
            try:
                os.environ["TYPESAFE_API_KEY"] = "environment-token"
                client = JevClient(config_path=path, opener=lambda *_a, **_kw: Response(valid_response()))
                self.assertEqual(client.api_key, "environment-token")
            finally:
                if old is None:
                    os.environ.pop("TYPESAFE_API_KEY", None)
                else:
                    os.environ["TYPESAFE_API_KEY"] = old

    def test_missing_key_has_specific_configuration_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(JevConfigurationError, "TYPESAFE_API_KEY"):
                JevClient(config_path=Path(tmp) / "missing", opener=lambda *_a, **_kw: None)

    def test_venice_uses_its_fixed_endpoint_and_key(self):
        seen = []
        client = JevClient(
            "venice", api_key="venice-key", opener=lambda req, **kw: (seen.append(req.full_url) or Response(valid_response()))
        )
        client.choose({}, {"continue": "go", "stop": "halt"}, "choose")
        self.assertEqual(seen, [VENICE_URL])

    def test_retries_rate_limit_with_bounded_backoff(self):
        calls, delays = [], []

        def opener(*_args, **_kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError("url", 429, "busy", {}, None)
            return Response(valid_response())

        result = self.client(opener, sleeper=delays.append).choose(
            {}, {"continue": "go", "stop": "halt"}, "choose"
        )
        self.assertEqual(result["choice"], "continue")
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.5, 1.0])

    def test_unauthorized_is_not_retried_and_body_is_not_exposed(self):
        calls = []
        secret_body = "server reflected secret-token"

        def opener(*_args, **_kwargs):
            calls.append(1)
            raise urllib.error.HTTPError("url", 401, secret_body, {}, None)

        with self.assertRaises(JevRequestError) as ctx:
            self.client(opener).choose({}, {"continue": "go", "stop": "halt"}, "choose")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret-token", str(ctx.exception))
        self.assertNotIn(secret_body, str(ctx.exception))

    def test_retryable_server_errors_stop_after_two_retries(self):
        calls, delays = [], []

        def opener(*_args, **_kwargs):
            calls.append(1)
            raise urllib.error.HTTPError("url", 503, "private body", {}, None)

        with self.assertRaisesRegex(JevRequestError, "HTTP 503"):
            self.client(opener, sleeper=delays.append).choose(
                {}, {"continue": "go", "stop": "halt"}, "choose"
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.5, 1.0])

    def test_rejects_unknown_model_and_choice(self):
        for response in (valid_response(model="other"), valid_response(answers={
            "next_action": {"type": "choice", "choice": "danger", "confidence": 1,
                            "probabilities": {"continue": 0.5, "stop": 0.5}}
        })):
            with self.subTest(response=response):
                with self.assertRaises(JevResponseError):
                    JevClient(api_key="x", opener=lambda *_a, _response=response, **_kw: Response(_response)).choose(
                        {}, {"continue": "go", "stop": "halt"}, "choose"
                    )

    def test_rejects_nonfinite_or_incomplete_probabilities(self):
        cases = [
            {"continue": float("nan"), "stop": 0},
            {"continue": float("inf"), "stop": 0},
            {"continue": 0.9},
            {"continue": 0.8, "stop": 0.1},
        ]
        for probs in cases:
            response = valid_response()
            response["answers"]["next_action"]["probabilities"] = probs
            with self.subTest(probs=probs), self.assertRaises(JevResponseError):
                self.client(lambda *_a, _r=response, **_kw: Response(_r)).choose(
                    {}, {"continue": "go", "stop": "halt"}, "choose"
                )

    def test_rejects_nonfinite_confidence_and_invalid_json(self):
        response = valid_response()
        response["answers"]["next_action"]["confidence"] = float("nan")
        with self.assertRaises(JevResponseError):
            self.client(lambda *_a, **_kw: Response(response)).choose(
                {}, {"continue": "go", "stop": "halt"}, "choose"
            )
        with self.assertRaisesRegex(JevResponseError, "invalid JSON"):
            self.client(lambda *_a, **_kw: type("R", (), {"read": lambda self: b"not json"})()).choose(
                {}, {"continue": "go", "stop": "halt"}, "choose"
            )


if __name__ == "__main__":
    unittest.main()
