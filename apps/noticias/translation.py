"""Tradução local de inglês para português; os originais nunca são substituídos."""

import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

MODEL_NAME = "Argos en-pt 1.9 · CTranslate2"


def financial_terms(source: str, target: str) -> str:
    """Corrige equivalências financeiras específicas, sem modificar números ou tickers."""
    if re.search(r"\bgrowth stocks?\b", source, re.I):
        determiners = {
            "esse": "essa",
            "este": "esta",
            "aquele": "aquela",
            "um": "uma",
            "o": "a",
            "do": "da",
            "desse": "dessa",
            "deste": "desta",
            "no": "na",
            "nesse": "nessa",
            "esses": "essas",
            "estes": "estas",
            "os": "as",
            "dos": "das",
            "nos": "nas",
        }

        def growth(match):
            det, plural = match.group(1), match.group(2)
            prefix = determiners.get(det.lower(), det.lower()) + " " if det else ""
            result = prefix + ("ações" if plural else "ação") + " de crescimento"
            return result[0].upper() + result[1:] if match.group(0)[0].isupper() else result

        target = re.sub(
            r"\b(?:(esse|este|aquele|um|o|do|desse|deste|no|nesse|esses|estes|os|dos|nos)\s+)?"
            r"estoque(s)? de crescimento\b",
            growth,
            target,
            flags=re.I,
        )
    if re.search(r"\bdid well\b", source, re.I):
        target = target.replace("fizeram bem", "tiveram bom desempenho")
    if re.search(r"\b(stock|stocks)\b.*\bcould soar\b", source, re.I | re.S):
        target = target.replace("poderia voar", "poderia subir fortemente")
    return target


@lru_cache(maxsize=1)
def _engine():
    import ctranslate2
    import sentencepiece

    root = Path(settings.NOTICIAS["MODELO_TRADUCAO"])
    tokenizer_file = next((p for p in [root / "sentencepiece.model", root / "bpe.model"] if p.exists()), None)
    if tokenizer_file is None:
        raise RuntimeError("Modelo de tradução local indisponível")
    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(tokenizer_file))
    engine = ctranslate2.Translator(
        str(root / "model"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=1
    )
    return tokenizer, engine


def translate(text: str) -> str:
    if not text.strip():
        return ""
    tokenizer, engine = _engine()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ü0-9])|\n+", text.strip())
    pieces = []
    # Limite por trecho para evitar truncar notícias longas durante a inferência.
    for sentence in sentences:
        words = sentence.split()
        for start in range(0, len(words), 80):
            pieces.append(" ".join(words[start : start + 80]))
    tokens = [tokenizer.encode(p, out_type=str) for p in pieces]
    results = engine.translate_batch(
        tokens,
        beam_size=3,
        max_batch_size=8,
        max_input_length=512,
        max_decoding_length=512,
        repetition_penalty=1.05,
    )
    translated = [
        re.sub(r"\s+", " ", tokenizer.decode(r.hypotheses[0]).replace("▁", " ")).strip() for r in results
    ]
    if any(not t for t in translated):
        raise RuntimeError("Tradução local vazia")
    return financial_terms(text, " ".join(translated))
