"""LLM client wrapper. DeepSeek is the initial provider.

DeepSeek exposes an OpenAI-compatible API, so we use the `openai` SDK and just
point base_url at DeepSeek. Adding another provider later = one more dict entry.
"""

from openai import OpenAI

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
}


class LLMClient:
    def __init__(self, provider="deepseek", api_key="", model=None):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider: %s" % provider)
        cfg = PROVIDERS[provider]
        self.provider = provider
        self.model = model or cfg["default_model"]
        self.client = OpenAI(api_key=api_key, base_url=cfg["base_url"])

    def translate(self, text):
        """Translate an English paragraph into Chinese, layout-neutral."""
        prompt = (
            "Translate the following academic English text into Simplified Chinese. "
            "Keep technical terms accurate. Return ONLY the translation, no notes.\n\n"
            + text
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    def define_word(self, word, context):
        """Return a Chinese definition + concept note for a word in context."""
        prompt = (
            'Explain the term "%s" as used in this sentence:\n"%s"\n\n'
            "Reply in Simplified Chinese with two short parts:\n"
            "1) 释义: a concise definition.\n"
            "2) 相关概念: 2-3 sentences of relevant background/conceptual knowledge."
            % (word, context)
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
