"""Banco SQLite: catálogo de documentos, execuções e eventos (histórico)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

# Etapas do pipeline, na ordem. Nesta fase o coletor cobre até "baixado";
# as demais já existem para o painel mostrar o caminho completo.
ETAPAS = ["descoberto", "baixado", "convertido", "validado", "revisado", "exportado"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id               TEXT PRIMARY KEY,      -- regras:<fileEntryID> | pdc:<chave>
    fonte            TEXT NOT NULL,         -- regras | pdc
    codigo           TEXT,                  -- '09' (caderno) | '1.6' (submódulo)
    titulo           TEXT,
    versao           TEXT,
    versao_arquivo   TEXT,                  -- versão lida do nome do arquivo
    vigencia_inicio  TEXT,                  -- AAAA-MM ou AAAA-MM-DD
    vigencia_fim     TEXT,
    situacao_site    TEXT,                  -- como o site classifica (Vigente, Vencido, aba...)
    tipo_documento   TEXT,
    grupo            TEXT,                  -- módulo do PdC ou tipo de lista
    ato_aneel        TEXT,
    ato_url          TEXT,
    url              TEXT NOT NULL,
    nome_arquivo     TEXT,
    extensao         TEXT,
    hash_site        TEXT,
    publicado_em     TEXT,
    selecionado      INTEGER NOT NULL DEFAULT 0,  -- entra no corpus desta fase?
    motivo_selecao   TEXT,
    etapa            TEXT NOT NULL DEFAULT 'descoberto',
    erro             TEXT,                  -- último erro (NULL = sem erro)
    tentativas       INTEGER NOT NULL DEFAULT 0,
    caminho_local    TEXT,
    sha256           TEXT,
    tamanho_bytes    INTEGER,
    alerta           TEXT,                  -- inconsistências encontradas
    descoberto_em    TEXT NOT NULL,
    visto_em         TEXT NOT NULL,         -- última vez que apareceu no site
    atualizado_em    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_doc_etapa ON documentos(etapa);
CREATE INDEX IF NOT EXISTS ix_doc_sel ON documentos(selecionado);

CREATE TABLE IF NOT EXISTS execucoes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    comando   TEXT NOT NULL,
    inicio    TEXT NOT NULL,
    fim       TEXT,
    status    TEXT NOT NULL,   -- rodando | ok | com_erros | falhou | interrompida
    resumo    TEXT             -- JSON com contagens
);

CREATE TABLE IF NOT EXISTS eventos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execucao_id   INTEGER,
    documento_id  TEXT,
    nivel         TEXT NOT NULL,   -- info | aviso | erro | mudanca
    mensagem      TEXT NOT NULL,
    em            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evt_exec ON eventos(execucao_id);

-- Monitor do Diário Oficial: cada edição (data + seção) lida e cada ato encontrado
CREATE TABLE IF NOT EXISTS dou_edicoes (
    data      TEXT NOT NULL,      -- AAAA-MM-DD
    secao     TEXT NOT NULL,      -- DO1, DO1E...
    status    TEXT NOT NULL,      -- lida | sem_edicao | erro
    materias  INTEGER,
    lida_em   TEXT NOT NULL,
    PRIMARY KEY (data, secao)
);
CREATE TABLE IF NOT EXISTS dou_avisos (
    id          TEXT PRIMARY KEY, -- idMateria do DOU
    data        TEXT NOT NULL,
    secao       TEXT,
    tipo        TEXT,             -- Resolução Normativa, Despacho...
    orgao       TEXT,
    identifica  TEXT,
    ementa      TEXT,
    assunto     TEXT,             -- PdC | Regras | PdC e Regras
    trecho      TEXT,
    url         TEXT,
    visto       INTEGER NOT NULL DEFAULT 0,
    encontrado_em TEXT NOT NULL
);
"""


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conectar(caminho: Path | None = None) -> sqlite3.Connection:
    caminho = Path(caminho or config.BANCO)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # painel lê enquanto o coletor escreve
    con.executescript(SCHEMA)
    return con


@contextmanager
def transacao(con: sqlite3.Connection):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def iniciar_execucao(con, comando: str) -> int:
    cur = con.execute(
        "INSERT INTO execucoes(comando, inicio, status) VALUES (?,?, 'rodando')",
        (comando, agora()),
    )
    con.commit()
    return cur.lastrowid


def finalizar_execucao(con, execucao_id: int, status: str, resumo: dict) -> None:
    con.execute(
        "UPDATE execucoes SET fim=?, status=?, resumo=? WHERE id=?",
        (agora(), status, json.dumps(resumo, ensure_ascii=False), execucao_id),
    )
    con.commit()


def evento(con, execucao_id, nivel: str, mensagem: str, documento_id: str | None = None) -> None:
    con.execute(
        "INSERT INTO eventos(execucao_id, documento_id, nivel, mensagem, em) VALUES (?,?,?,?,?)",
        (execucao_id, documento_id, nivel, mensagem, agora()),
    )
    con.commit()


# Campos que vêm do site (a descoberta pode atualizá-los a cada rodada)
CAMPOS_SITE = [
    "fonte", "codigo", "titulo", "versao", "versao_arquivo", "vigencia_inicio", "vigencia_fim",
    "situacao_site", "tipo_documento", "grupo", "ato_aneel", "ato_url", "url", "nome_arquivo",
    "extensao", "hash_site", "publicado_em", "selecionado", "motivo_selecao", "alerta",
]


def registrar_descoberta(con, execucao_id: int, doc: dict) -> str:
    """Insere ou atualiza um documento. Retorna 'novo', 'alterado' ou 'igual'.

    Se a URL ou o hash publicado mudarem, o documento volta para 'descoberto'
    para ser baixado de novo; o arquivo antigo fica no disco (histórico)."""
    ts = agora()
    atual = con.execute("SELECT * FROM documentos WHERE id=?", (doc["id"],)).fetchone()
    valores = {c: doc.get(c) for c in CAMPOS_SITE}
    if atual is None:
        cols = ["id", *CAMPOS_SITE, "etapa", "descoberto_em", "visto_em", "atualizado_em"]
        con.execute(
            f"INSERT INTO documentos({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [doc["id"], *valores.values(), "descoberto", ts, ts, ts],
        )
        return "novo"

    mudou_arquivo = (atual["url"] != valores["url"]) or (
        valores["hash_site"] and atual["hash_site"] and atual["hash_site"] != valores["hash_site"]
    )
    mudou_algo = any(str(atual[c]) != str(valores[c]) for c in CAMPOS_SITE)
    sets = ", ".join(f"{c}=?" for c in CAMPOS_SITE)
    params = [*valores.values(), ts]
    extra = ""
    if mudou_arquivo:
        extra = ", etapa='descoberto', erro=NULL, tentativas=0"
    con.execute(
        f"UPDATE documentos SET {sets}, visto_em=?{extra}"
        + (", atualizado_em=?" if mudou_algo else "")
        + " WHERE id=?",
        [*params, *([ts] if mudou_algo else []), doc["id"]],
    )
    if mudou_arquivo:
        evento(con, execucao_id, "mudanca", "Arquivo publicado mudou no site; será baixado de novo", doc["id"])
        return "alterado"
    return "alterado" if mudou_algo else "igual"
