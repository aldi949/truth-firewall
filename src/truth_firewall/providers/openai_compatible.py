"""OpenAI-compatible chat completions client. Stdlib HTTP only."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from truth_firewall.claims.prompts import EXTRACTION_SYSTEM, REWRITE_SYSTEM, VERIFY_SYSTEM
from truth_firewall.constants import MAX_LLM_EVIDENCE_SNIPPET_CHARS
from truth_firewall.errors import ProviderError, ProviderNotConfigured
from truth_firewall.redaction import redact_text, redact_value


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60,
        transport: Any = None,
    ) -> None:
        if not api_key:
            raise ProviderNotConfigured("API key is missing")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._transport = transport or urllib.request.urlopen

    @classmethod
    def from_env(cls) -> "OpenAICompatibleProvider":
        api_key = os.environ.get("TRUTH_FIREWALL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        base_url = (
            os.environ.get("TRUTH_FIREWALL_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        model = os.environ.get("TRUTH_FIREWALL_MODEL") or "gpt-4o-mini"
        timeout = float(os.environ.get("TRUTH_FIREWALL_TIMEOUT_SECONDS") or "60")
        if not api_key:
            raise ProviderNotConfigured("TRUTH_FIREWALL_API_KEY or OPENAI_API_KEY is not set")
        return cls(api_key=api_key, base_url=base_url, model=model, timeout=timeout)

    def extract_claims(self, response_text: str) -> list[dict[str, Any]]:
        user = json.dumps({"untrusted_response": redact_text(response_text)}, ensure_ascii=False)
        payload = self._chat(EXTRACTION_SYSTEM, user)
        claims = payload.get("claims", payload if isinstance(payload, list) else [])
        if not isinstance(claims, list):
            raise ProviderError("provider claim payload is not a list")
        return [item for item in claims if isinstance(item, dict)]

    def verify_claim_evidence(
        self, claim: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        slim = []
        for item in evidence:
            text = json.dumps(redact_value(item), ensure_ascii=False)
            slim.append(text[:MAX_LLM_EVIDENCE_SNIPPET_CHARS])
        user = json.dumps(
            {"untrusted_claim": redact_value(claim), "untrusted_evidence": slim},
            ensure_ascii=False,
        )
        return self._chat(VERIFY_SYSTEM, user)

    def rewrite_response(self, response_text: str, assessments: list[dict[str, Any]]) -> str:
        user = json.dumps(
            {
                "untrusted_response": redact_text(response_text),
                "assessments": redact_value(assessments),
            },
            ensure_ascii=False,
        )
        payload = self._chat(REWRITE_SYSTEM, user)
        rewritten = payload.get("rewritten")
        if not isinstance(rewritten, str):
            raise ProviderError("provider rewrite payload has no rewritten text")
        return rewritten

    def _chat(self, system: str, user: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._transport(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = redact_text(exc.read().decode("utf-8", errors="replace")[:500])
            raise ProviderError(f"provider HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"provider network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError("provider timed out") from exc
        try:
            parsed = json.loads(raw)
            content = parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("provider returned an unreadable completion") from exc
        return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("provider content was not JSON") from exc
    if not isinstance(loaded, dict):
        raise ProviderError("provider JSON was not an object")
    return loaded
