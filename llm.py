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

    def study_summary_stream(self, papers):
        """papers = [(name, text), ...]. Yield a Chinese study summary + advice:
        per-paper summary, links between them, and a step-by-step learning plan."""
        parts = []
        for i, (name, text) in enumerate(papers, 1):
            parts.append("【论文%d：%s】\n%s" % (i, name, text))
        body = "\n\n".join(parts)
        prompt = (
            "你是一位学术导师。下面是我挑选要学习的论文内容。请用简体中文：\n"
            "1) 对每篇论文给出简明摘要（研究问题、方法、主要结论）；\n"
            "2) 指出它们的共同主题与相互联系；\n"
            "3) 给出循序渐进的学习建议：建议的阅读顺序、需要补充的背景知识、"
            "每篇的重点章节、以及可延伸的研究方向。\n"
            "条理清晰，使用小标题（用 Markdown）。\n\n" + body
        )
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def explain_stream(self, text):
        """Yield a Chinese explanation of a selected passage: what it means plus
        relevant concepts/background. This powers the selection popup (it is a
        look-up / explanation, NOT a translation)."""
        prompt = (
            "用简体中文解释下面这段选中的学术内容：\n"
            "1) 先简要说明它在说什么（含义/要点）；\n"
            "2) 再补充相关的概念、背景或知识点，帮助理解。\n"
            "只输出解释，不要逐字翻译原文。\n\n选中内容：\n" + text
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
