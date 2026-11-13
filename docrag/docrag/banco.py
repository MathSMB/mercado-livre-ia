"""Banco SQLite do docrag: um arquivo por perfil, em <trabalho>/docrag.db."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id              INTEGER PRIMARY KEY,
    arquivo         TEXT NOT NULL,           -- nome do PDF na pasta de entrada
    sha256          TEXT NOT NULL UNIQUE,
    paginas         INTEGER,
    tipo            TEXT,                    -- tipo do perfil (ex. pdc, regra)
    documento       TEXT,                    -- identificador (ex. 1.6, 05)
    titulo          TEXT,
    versao          TEXT,
    vigencia_inicio TEXT,
    vigencia_fim    TEXT,
    ato             TEXT,                    -- ato de aprovação (ex. REN ANEEL)
    metadados_origem TEXT,                   -- 'extraido' | 'documentos.csv' | 'painel'
    metadados_ok    INTEGER DEFAULT 0,       -- 1 = conferido por uma pessoa
    etapa           TEXT DEFAULT 'importado',-- importado | convertido | verificado | exportado
    status_qualidade TEXT,                   -- ok | aviso | falha
    aceito_em       TEXT,                    -- aceite humano dos avisos/falhas
    aceito_nota     TEXT,
    conversor_principal TEXT,
    final_sha256    TEXT,                    -- hash do texto final gerado (detecta correção à mão)
    erro            TEXT,
    importado_em    TEXT,
    atualizado_em   TEXT
);
CREATE TABLE IF NOT EXISTS conversoes (
    documento_id INTEGER REFERENCES documentos(id),
    conversor    TEXT,
    versao       TEXT,
    segundos     REAL,
    caminho      TEXT,
    avisos       TEXT,
    em           TEXT,
    PRIMARY KEY (documento_id, conversor)
);
CREATE TABLE IF NOT EXISTS verificacoes (
    id           INTEGER PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id),
    codigo       TEXT,
    nivel        TEXT,
    pagina       INTEGER,
    mensagem     TEXT,
    detalhe      TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id),
    ordem        INTEGER,
    secao        TEXT,
    numero       TEXT,
    caminho      TEXT,
    pagina_inicio INTEGER,
    pagina_fim   INTEGER,
    palavras     INTEGER,
    parte        INTEGER,
    de_partes    INTEGER,
    texto        TEXT
);
CREATE TABLE IF NOT EXISTS execucoes (
    id      INTEGER PRIMARY KEY,
    comando TEXT,
    inicio  TEXT,
    fim     TEXT,
    status  TEXT,                            -- rodando | ok | com_erros | falhou
    resumo  TEXT
);
CREATE TABLE IF NOT EXISTS eventos (
    id           INTEGER PRIMARY KEY,
    execucao_id  INTEGER,
    documento_id INTEGER,
    em           TEXT,
    nivel        TEXT,                       -- info | aviso | erro
    mensagem     TEXT
);
CREATE TABLE IF NOT EXISTS avaliacoes (
    id          INTEGER PRIMARY KEY,
    em          TEXT,
    pergunta_id TEXT,
    pergunta    TEXT,
    resultado   TEXT,                        -- achou | fora_do_top | trecho_ausente | doc_ausente
    posicao     INTEGER,
    chunk       TEXT,
    detalhe     TEXT
);
"""

CAMPOS_METADADOS = ["tipo", "documento", "titulo", "versao", "vigencia_inicio", "vigencia_fim", "ato"]


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(ESQUEMA)
    return con


@contextmanager
def aberto(caminho: Path):
    con = conectar(caminho)
    try:
        yield con
        con.commit()
    finally:
        con.close()


class Execucao:
    """Registra uma execução do pipeline e seus eventos (lidos pelo painel)."""

    def __init__(self, con: sqlite3.Connection, comando: str):
        self.con = con
        self.erros = 0
        self.id = con.execute("INSERT INTO execucoes (comando, inicio, status) VALUES (?, ?, 'rodando')",
                              (comando, agora())).lastrowid
        con.commit()

    def evento(self, nivel: str, mensagem: str, documento_id: int | None = None):
        if nivel == "erro":
            self.erros += 1
        print(f"[{nivel}] {mensagem}", flush=True)
        self.con.execute("INSERT INTO eventos (execucao_id, documento_id, em, nivel, mensagem) VALUES (?,?,?,?,?)",
                         (self.id, documento_id, agora(), nivel, mensagem))
        self.con.commit()

    def terminar(self, resumo: dict, falhou: bool = False):
        status = "falhou" if falhou else "com_erros" if self.erros else "ok"
        self.con.execute("UPDATE execucoes SET fim=?, status=?, resumo=? WHERE id=?",
                         (agora(), status, json.dumps(resumo, ensure_ascii=False), self.id))
        self.con.commit()
