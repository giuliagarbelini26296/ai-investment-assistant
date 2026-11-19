"""Filtro de log: mascara segredos (token do bot do Telegram, chaves em query string) em qualquer mensagem."""

from __future__ import annotations

import logging
import re

_PADROES = (
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+/"), "/bot<oculto>/"),
    (re.compile(r"([?&](?:apikey|api_key|token|key)=)[^&\s\"']+", re.IGNORECASE), r"\1<oculto>"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._-]+"), r"\1<oculto>"),
)


def mascarar(texto: str) -> str:
    for padrao, troca in _PADROES:
        texto = padrao.sub(troca, texto)
    return texto


class MascararSegredos(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = mascarar(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - nunca derrubar o log
            pass
        return True
