"""Importa PDFs baixados à mão do site da CCEE (o site bloqueia coleta automática).

Coloque os arquivos em dados/entrada/ (ou passe outra pasta) e rode
    python -m ccee.pipeline importar
O nome do arquivo, como o site o publica, já traz caderno/submódulo e versão:
    Regras:  "05 - Contratos_2026.6.4_JUN.pdf"
    PdCs:    "1.6_-_Comercializacao_varejista_v6.0.pdf"
O arquivo é copiado para dados/pdf/<fonte>/<código>/ e entra no fluxo como "Baixado".
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from . import config, db
from .download import ASSINATURAS, nome_seguro

RX_REGRA = re.compile(r"^(\d{2})\s*-\s*(.+?)_(\d{4}\.\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
RX_PDC = re.compile(r"^(?:subm[oó]dulo[\s_]*)?(\d{1,2}\.\d{1,2})[\s_\-–]+(.+?)[\s_\-]+v(?:ers[aã]o[\s_]*)?(\d+\.\d+)",
                    re.IGNORECASE)
EXTENSOES = {".pdf": "PDF", ".zip": "ZIP"}


def identificar(nome: str) -> dict:
    base = Path(nome).stem
    if m := RX_REGRA.match(base):
        return {"fonte": "regras", "codigo": m.group(1), "titulo": f"{m.group(1)} - {m.group(2).strip()}",
                "versao": m.group(3), "versao_arquivo": m.group(3)}
    if m := RX_PDC.match(base):
        titulo = m.group(2).replace("_", " ").strip(" -")
        return {"fonte": "pdc", "codigo": m.group(1), "titulo": f"Submódulo {m.group(1)} - {titulo}",
                "versao": m.group(3), "versao_arquivo": m.group(3)}
    return {"fonte": "outros", "codigo": "outros", "titulo": base, "versao": None, "versao_arquivo": None,
            "alerta": "Nome do arquivo não reconhecido como caderno de Regras nem submódulo de PdC; confira."}


def importar_arquivo(con, exec_id: int, arq: Path, destino_base: Path | None = None) -> str:
    destino_base = destino_base or config.PASTA_PDF
    tipo = EXTENSOES.get(arq.suffix.lower())
    conteudo = arq.read_bytes()
    if not tipo:
        raise ValueError(f"Extensão não suportada: {arq.suffix}")
    if not conteudo.startswith(ASSINATURAS[tipo]):
        raise ValueError(f"Conteúdo não parece {tipo} (arquivo corrompido ou página de erro salva)")
    sha = hashlib.sha256(conteudo).hexdigest()
    info = identificar(arq.name)
    nome = nome_seguro(arq.name)
    pasta = Path(destino_base) / info["fonte"] / info["codigo"]
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{sha[:8]}_{nome}"
    if not caminho.exists():
        tmp = caminho.with_suffix(caminho.suffix + ".parcial")
        shutil.copyfile(arq, tmp)
        tmp.replace(caminho)
    doc = {
        **info,
        "id": f"{info['fonte']}:{info['codigo']}:importado" if info["fonte"] != "outros" else f"outros:{sha[:12]}",
        "url": f"importado:{nome}", "nome_arquivo": nome, "extensao": tipo, "hash_site": sha,
        "selecionado": 1, "motivo_selecao": "Importado manualmente (baixado do site pela pessoa)",
        "situacao_site": None,
    }
    resultado = db.registrar_descoberta(con, exec_id, doc)
    con.execute(
        "UPDATE documentos SET etapa=CASE WHEN etapa='descoberto' THEN 'baixado' ELSE etapa END, erro=NULL,"
        " caminho_local=?, sha256=?, tamanho_bytes=?, atualizado_em=? WHERE id=?",
        (str(caminho), sha, len(conteudo), db.agora(), doc["id"]),
    )
    if info.get("alerta"):
        db.evento(con, exec_id, "aviso", info["alerta"], doc["id"])
    return resultado


def importar_pasta(con, exec_id: int, pasta: Path, log=print):
    from collections import Counter
    c = Counter()
    arquivos = sorted(p for p in Path(pasta).glob("*") if p.is_file() and not p.name.startswith("."))
    log(f"{len(arquivos)} arquivo(s) em {pasta}")
    for arq in arquivos:
        try:
            with db.transacao(con):
                r = importar_arquivo(con, exec_id, arq)
            c[f"importados_{r}"] += 1
            log(f"ok    {arq.name} ({r})")
        except Exception as e:  # noqa: BLE001
            db.evento(con, exec_id, "erro", f"Falha ao importar {arq.name}: {e}")
            c["erros"] += 1
            log(f"ERRO  {arq.name}: {e}")
    return c
