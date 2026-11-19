"""Segredos nunca aparecem nos logs (token do Telegram, chaves em query string)."""

import logging

from config.log_filters import MascararSegredos, mascarar


def test_mascara_token_do_telegram_e_chaves():
    assert mascarar("POST https://api.telegram.org/bot8302854177:AAG1_Kj6ZTYT-z8g/sendMessage") == (
        "POST https://api.telegram.org/bot<oculto>/sendMessage"
    )
    assert mascarar("GET https://api.twelvedata.com/quote?symbol=TTWO&apikey=abc123&x=1") == (
        "GET https://api.twelvedata.com/quote?symbol=TTWO&apikey=<oculto>&x=1"
    )
    assert mascarar("Authorization: Bearer abc.def-ghi") == "Authorization: Bearer <oculto>"
    assert (
        mascarar("GET https://olinda.bcb.gov.br/x?%24format=json")
        == "GET https://olinda.bcb.gov.br/x?%24format=json"
    )


def test_filtro_aplica_em_registros_com_args():
    f = MascararSegredos()
    rec = logging.LogRecord(
        "httpx",
        logging.INFO,
        "",
        0,
        'HTTP Request: %s %s "%s"',
        ("POST", "https://api.telegram.org/bot1:AB/sendMessage", "200"),
        None,
    )
    assert f.filter(rec) is True
    assert rec.getMessage() == 'HTTP Request: POST https://api.telegram.org/bot<oculto>/sendMessage "200"'
