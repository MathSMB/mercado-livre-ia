"""Testa, a partir deste computador, se cada fonte aceita acesso do coletor.

    python -m ccee.testar_fontes

Faz UMA requisição por endereço, com a identificação normal do coletor, e mostra o
código HTTP. 200 = acessível; 403 = a fonte bloqueia programas; erro = sem conexão.
"""
from __future__ import annotations

import requests

from . import config

FONTES = [
    ("CCEE - página de PdCs", config.URL_PDCS),
    ("CCEE - página de Regras", config.URL_REGRAS),
    ("CCEE - Dados Abertos (API CKAN)", "https://dadosabertos.ccee.org.br/api/3/action/package_list"),
    ("ANEEL - portal gov.br", "https://www.gov.br/aneel/pt-br"),
    ("ANEEL - Biblioteca/legislação", "https://biblioteca.aneel.gov.br/"),
    ("ANEEL - Dados Abertos (API CKAN)", "https://dadosabertos.aneel.gov.br/api/3/action/package_list"),
    ("DOU - leitura do jornal", "https://www.in.gov.br/leiturajornal"),
    ("DOU - INLABS (download oficial)", "https://inlabs.in.gov.br/"),
]


def main() -> int:
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    print(f"{'Fonte':38} {'Resultado':10} Tipo")
    for nome, url in FONTES:
        try:
            r = s.get(url, timeout=30)
            res, tipo = str(r.status_code), r.headers.get("content-type", "")[:40]
        except requests.RequestException as e:
            res, tipo = "erro", type(e).__name__
        print(f"{nome:38} {res:10} {tipo}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
