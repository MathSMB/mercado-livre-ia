"""Cliente HTTP educado: intervalo entre requisições, novas tentativas e timeout."""
from __future__ import annotations

import time

import requests

from . import config


class Cliente:
    def __init__(self, intervalo: float = config.INTERVALO_SEGUNDOS):
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = config.USER_AGENT
        self.intervalo = intervalo
        self._ultima = 0.0

    def _esperar(self):
        falta = self.intervalo - (time.monotonic() - self._ultima)
        if falta > 0:
            time.sleep(falta)
        self._ultima = time.monotonic()

    def requisitar(self, metodo: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", config.TIMEOUT_SEGUNDOS)
        ultimo_erro: Exception | None = None
        for tentativa in range(1, config.TENTATIVAS + 1):
            self._esperar()
            try:
                resp = self.sessao.request(metodo, url, **kw)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                ultimo_erro = e
                if tentativa < config.TENTATIVAS:
                    time.sleep(2 ** tentativa)  # 2s, 4s...
        raise ultimo_erro  # type: ignore[misc]

    def get(self, url, **kw):
        return self.requisitar("GET", url, **kw)

    def post(self, url, **kw):
        return self.requisitar("POST", url, **kw)
