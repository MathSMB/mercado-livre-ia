"""Monitor do Diário Oficial da União: avisa quando sai ato sobre PdCs ou Regras.

    python -m ccee.monitor_dou            # lê as edições que faltam (até hoje)
    python -m ccee.monitor_dou --dias 60  # olha mais para trás

Versões novas de PdCs e Regras de Comercialização são aprovadas pela ANEEL e o ato
sai no DOU. Usamos o INLABS, serviço oficial da Imprensa Nacional feito para download
automatizado (https://github.com/Imprensa-Nacional/inlabs). O site da CCEE não é
acessado aqui.
"""
from __future__ import annotations

import argparse
import html
import io
import os
import re
import sys
import time
import unicodedata
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import requests

from . import config, db

TERMOS = {
    "PdC": re.compile(r"procedimentos? de comercializa[cç][aã]o", re.IGNORECASE),
    "Regras": re.compile(r"regras de comercializa[cç][aã]o", re.IGNORECASE),
}
RX_TAGS = re.compile(r"<[^>]+>")


class SemCredenciais(RuntimeError):
    pass


def credenciais() -> tuple[str, str]:
    email, senha = os.environ.get("INLABS_EMAIL"), os.environ.get("INLABS_SENHA")
    arq = config.INLABS_CREDENCIAIS
    if not (email and senha) and arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            k, _, v = linha.partition("=")
            if k.strip().lower() == "email":
                email = v.strip()
            elif k.strip().lower() == "senha":
                senha = v.strip()
    if not (email and senha):
        raise SemCredenciais(
            f"Credenciais do INLABS não encontradas. Crie o arquivo {arq} com as linhas\n"
            "email=seu_email\nsenha=sua_senha\n(conta gratuita em https://inlabs.in.gov.br)")
    return email, senha


class ClienteInlabs:
    def __init__(self, sessao=None):
        self.s = sessao or requests.Session()
        self.s.headers["User-Agent"] = config.USER_AGENT

    def entrar(self):
        email, senha = credenciais()
        self.s.post(config.INLABS_LOGIN, data={"email": email, "password": senha},
                    timeout=config.TIMEOUT_SEGUNDOS)
        if not self.s.cookies.get("inlabs_session_cookie"):
            raise RuntimeError("O INLABS recusou o login. Confira e-mail e senha em " + str(config.INLABS_CREDENCIAIS))

    def baixar_zip(self, dia: date, secao: str) -> bytes | None:
        d = dia.isoformat()
        url = f"{config.INLABS_DOWNLOAD}{d}&dl={d}-{secao}.zip"
        r = self.s.get(url, headers={"origem": "736372697074"}, timeout=config.TIMEOUT_SEGUNDOS * 3)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        if not r.content.startswith(b"PK"):
            return None  # sem edição nesse dia (o INLABS devolve página HTML)
        return r.content


def _sem_acento(t: str) -> str:
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


def _texto(el) -> str:
    return re.sub(r"\s+", " ", html.unescape(RX_TAGS.sub(" ", el.text or ""))).strip() if el is not None else ""


def materias_do_zip(conteudo: bytes):
    """Gera um dict por matéria (ato) do ZIP de XMLs de uma edição."""
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(".xml"):
                continue
            try:
                raiz = ET.fromstring(z.read(nome))
            except ET.ParseError:
                continue
            for art in raiz.iter("article"):
                corpo = art.find("body")
                yield {
                    "id": art.get("idMateria") or art.get("id") or nome,
                    "secao": art.get("pubName"),
                    "tipo": art.get("artType"),
                    "orgao": art.get("artCategory"),
                    "url": art.get("pdfPage"),
                    "identifica": _texto(corpo.find("Identifica")) if corpo is not None else "",
                    "ementa": _texto(corpo.find("Ementa")) if corpo is not None else "",
                    "texto": _texto(corpo.find("Texto")) if corpo is not None else "",
                }


def avaliar(m: dict) -> dict | None:
    """Devolve o aviso se a matéria fala de PdCs ou Regras de Comercialização."""
    tudo = " ".join([m["identifica"], m["ementa"], m["texto"]])
    achados = [k for k, rx in TERMOS.items() if rx.search(tudo) or rx.search(_sem_acento(tudo))]
    if not achados:
        return None
    rx = TERMOS[achados[0]]
    pos = (rx.search(tudo) or rx.search(_sem_acento(tudo))).start()
    trecho = tudo[max(0, pos - 200): pos + 300].strip()
    return {**m, "assunto": " e ".join(achados), "trecho": trecho}


def dias_pendentes(con, hoje: date, dias_iniciais: int) -> list[date]:
    ultima = con.execute("SELECT MAX(data) FROM dou_edicoes WHERE status IN ('lida','sem_edicao')").fetchone()[0]
    inicio = date.fromisoformat(ultima) + timedelta(days=1) if ultima else hoje - timedelta(days=dias_iniciais - 1)
    erros = [date.fromisoformat(r[0]) for r in con.execute("SELECT DISTINCT data FROM dou_edicoes WHERE status='erro'")]
    dias = {inicio + timedelta(days=i) for i in range((hoje - inicio).days + 1)} | set(erros)
    return sorted(d for d in dias if d.weekday() < 5 or d in erros)  # DOU normal não sai no fim de semana


def executar(dias: int | None = None, hoje: date | None = None, cliente=None, log=print) -> int:
    hoje = hoje or date.today()
    con = db.conectar()
    exec_id = db.iniciar_execucao(con, "monitor-dou")
    novos = lidas = 0
    status = "ok"
    try:
        cliente = cliente or ClienteInlabs()
        cliente.entrar()
        if dias:
            pendentes = [hoje - timedelta(days=i) for i in range(dias - 1, -1, -1)]
            pendentes = [d for d in pendentes if d.weekday() < 5]
        else:
            pendentes = dias_pendentes(con, hoje, config.DOU_DIAS_INICIAIS)
        log(f"{len(pendentes)} dia(s) do DOU para ler")
        for dia in pendentes:
            for secao in config.DOU_SECOES:
                try:
                    conteudo = cliente.baixar_zip(dia, secao)
                except requests.RequestException as e:
                    _edicao(con, dia, secao, "erro", None)
                    db.evento(con, exec_id, "erro", f"DOU {dia} {secao}: {e}")
                    status = "com_erros"
                    continue
                if conteudo is None:
                    _edicao(con, dia, secao, "sem_edicao", 0)
                    continue
                n = 0
                for m in materias_do_zip(conteudo):
                    n += 1
                    aviso = avaliar(m)
                    if aviso and _registrar_aviso(con, dia, aviso):
                        novos += 1
                        msg = f"DOU {dia:%d/%m/%Y}: {aviso['tipo'] or ''} {aviso['identifica'][:120]} ({aviso['assunto']})"
                        db.evento(con, exec_id, "mudanca", msg)
                        log("NOVO  " + msg)
                _edicao(con, dia, secao, "lida", n)
                lidas += 1
                log(f"{dia} {secao}: {n} matérias lidas")
                time.sleep(1)
    except SemCredenciais as e:
        status = "falhou"
        db.evento(con, exec_id, "erro", str(e).splitlines()[0])
        log(str(e))
    except Exception as e:  # noqa: BLE001
        status = "falhou"
        db.evento(con, exec_id, "erro", f"Monitor do DOU falhou: {e}")
        log(f"ERRO: {e}")
    finally:
        db.finalizar_execucao(con, exec_id, status, {"edicoes_lidas": lidas, "avisos_novos": novos})
        log(f"Monitor do DOU terminou: {status}; {lidas} edições lidas, {novos} aviso(s) novo(s)")
    return 0 if status == "ok" else 1


def _edicao(con, dia, secao, status, materias):
    con.execute("INSERT OR REPLACE INTO dou_edicoes(data, secao, status, materias, lida_em) VALUES (?,?,?,?,?)",
                (dia.isoformat(), secao, status, materias, db.agora()))
    con.commit()


def _registrar_aviso(con, dia, a) -> bool:
    cur = con.execute(
        "INSERT OR IGNORE INTO dou_avisos(id, data, secao, tipo, orgao, identifica, ementa, assunto, trecho, url,"
        " encontrado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (a["id"], dia.isoformat(), a["secao"], a["tipo"], a["orgao"], a["identifica"], a["ementa"],
         a["assunto"], a["trecho"], a["url"], db.agora()),
    )
    con.commit()
    return cur.rowcount == 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ccee.monitor_dou", description=__doc__.splitlines()[0])
    ap.add_argument("--dias", type=int, help="lê os últimos N dias, mesmo os já lidos")
    a = ap.parse_args(argv)
    return executar(a.dias, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
