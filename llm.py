"""LLM client wrapper. DeepSeek is the initial provider.

DeepSeek exposes an OpenAI-compatible API, so we use the `openai` SDK and point
base_url at DeepSeek. Both translate and define_word are STREAMING generators so
the UI can render output token-by-token.
"""

from openai import OpenAI

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",          # OpenAI-compatible
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "default_model": "gpt-4o-mini",
    },
}


def models_for(provider):
    return PROVIDERS.get(provider, {}).get("models", [])


class LLMClient:
    def __init__(self, provider="deepseek", api_key="", model=None):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider: %s" % provider)
        cfg = PROVIDERS[provider]
        self.provider = provider
        self.model = model or cfg["default_model"]
        self.client = OpenAI(api_key=api_key, base_url=cfg["base_url"])

    def translate_stream(self, text):
        """Yield Chinese translation token-by-token for one English paragraph."""
        prompt = (
            "Translate the following academic English text into Simplified Chinese.\n"
            "Rules:\n"
            "- Keep technical terms accurate.\n"
            "- Preserve all special symbols, mathematical notation, formulas, "
            "inline equations, citation markers (e.g. [12], (Smith et al., 2020)), "
            "URLs, and numbers exactly as they appear — do NOT translate or alter them.\n"
            "- Preserve the original spacing and line structure where meaningful.\n"
            "- Return ONLY the translation, with no notes or explanations.\n\n"
            + text
        )
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def define_word_stream(self, word, context):
        """Yield a Chinese definition + concept note token-by-token."""
        prompt = (
            'Explain the term "%s" as used in this sentence:\n"%s"\n\n'
            "Reply in Simplified Chinese with two short parts:\n"
            "1) 释义: a concise definition.\n"
            "2) 相关概念: 2-3 sentences of relevant background/conceptual knowledge."
            % (word, context)
        )
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
