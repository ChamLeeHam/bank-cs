"""Add a reviewed banking term to a question before scoring FAQ passages."""

import re
import unicodedata
from functools import lru_cache

import yaml

from src.schema.config import ROOT_DIR

_CJK = re.compile(r"[\u3400-\u9fff]")


def _normalize(text):
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _pattern(phrases):
    alternatives = []
    for phrase in phrases:
        phrase = _normalize(phrase)
        prefix = r"(?<![a-z0-9])" if phrase[0].isascii() else ""
        suffix = r"(?![a-z0-9])" if phrase[-1].isascii() else ""
        alternatives.append(prefix + re.escape(phrase) + suffix)
    return re.compile("|".join(alternatives)) if alternatives else None


@lru_cache(maxsize=1)
def _rerank_hints():
    path = ROOT_DIR / "data" / "banking_terminology.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    hints = []
    for document in data["documents"].values():
        for term in document["terms"]:
            hint = term.get("rerank_hint")
            if hint:
                aliases = term["aliases_zh"] + term["aliases_en"] + term.get("aliases_mixed", [])
                cues = tuple(_pattern(group) for group in hint["cues"])
                scope = _pattern(hint.get("exclude", []))
                faq_cues = _pattern(hint.get("faq", {}).get("cues", []))
                hints.append((hint, _pattern(aliases), cues, scope, faq_cues))
    return hints


def _match(text):
    matches = [
        (hint, faq_cues) for hint, alias, cues, exclude, faq_cues in _rerank_hints()
        if not (exclude and exclude.search(text))
        and (alias.search(text) or all(cue.search(text) for cue in cues))
    ]
    return matches[0] if len(matches) == 1 else (None, None)


def with_banking_term_hint(question):
    """Keep the full question. Append a hint only when one reviewed scenario matches."""
    hint, _ = _match(_normalize(question))
    if not hint:
        return question
    language = "zh" if _CJK.search(question) else "en"
    return f"{question}\n{hint[language]}"


def banking_term_faq_intent(question):
    """Supply a knowledge intent only for reviewed, explicit guidance questions."""
    text = _normalize(question)
    hint, faq_cues = _match(text)
    return hint["faq"]["intent"] if faq_cues and faq_cues.search(text) else None
