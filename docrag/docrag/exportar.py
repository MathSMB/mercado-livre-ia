"""Monta a pasta de saída para o SharePoint / Copilot Studio.

saida/
  sharepoint/                 <- suba esta pasta inteira numa biblioteca do SharePoint
    <tipo>/<documento>_v<versão>/0001-<seção>.md
    glossario.md
  outras-versoes/             <- versões não vigentes hoje (não suba junto, para o Copilot não misturar)
  manifesto.csv               <- uma linha por chunk, com os metadados (serve para colunas do SharePoint)
  glossario.csv
  relatorios/qualidade-<doc>.md
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from . import texto as tx

COLUNAS_MANIFESTO = ["arquivo", "chunk_id", "tipo", "documento", "titulo", "versao", "vigencia_inicio", "vigencia_fim",
                     "ato", "secao", "caminho", "pagina_inicio", "pagina_fim", "parte", "palavras", "pdf_origem",
                     "pdf_sha256"]


def data(valor: str | None) -> date | None:
    if not valor:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%Y", "%Y-%m"):
        try:
            return datetime.strptime(valor.strip(), fmt).date()
        except ValueError:
            continue
    return None


def rotulo_documento(perfil, doc: dict) -> str:
    tipo = perfil.tipo(doc.get("tipo"))
    nome = f"{tipo.rotulo if tipo else (doc.get('tipo') or 'Documento')} {doc.get('documento') or ''}".strip()
    return f"{nome} – {doc['titulo']}" if doc.get("titulo") else nome


def cabecalho(perfil, doc: dict, ch: dict) -> str:
    """Linha de contexto legível no topo de cada chunk: é o que faz a busca achar e o Copilot citar certo."""
    partes = [f"**{rotulo_documento(perfil, doc)}**"]
    if doc.get("versao"):
        partes.append(f"versão {doc['versao']}")
    if doc.get("vigencia_inicio"):
        vig = f"vigência a partir de {doc['vigencia_inicio']}"
        if doc.get("vigencia_fim"):
            vig += f" até {doc['vigencia_fim']}"
        partes.append(vig)
    if doc.get("ato"):
        partes.append(f"aprovado por {doc['ato']}")
    caminho = ch["caminho"] if isinstance(ch["caminho"], list) else json.loads(ch["caminho"])
    partes.append("seção " + " > ".join(caminho))
    pags = f"{ch['pagina_inicio']}" if ch["pagina_inicio"] == ch["pagina_fim"] else f"{ch['pagina_inicio']}–{ch['pagina_fim']}"
    partes.append(f"página{'s' if '–' in pags else ''} {pags}")
    return " · ".join(partes)


def _yaml(valor) -> str:
    return json.dumps("" if valor is None else str(valor), ensure_ascii=False)


def texto_chunk(perfil, doc: dict, ch: dict) -> str:
    caminho = ch["caminho"] if isinstance(ch["caminho"], list) else json.loads(ch["caminho"])
    meta = {
        "documento": rotulo_documento(perfil, doc), "tipo": doc.get("tipo"), "codigo": doc.get("documento"),
        "versao": doc.get("versao"), "vigencia_inicio": doc.get("vigencia_inicio"),
        "vigencia_fim": doc.get("vigencia_fim"), "ato": doc.get("ato"), "secao": ch["secao"],
        "caminho": " > ".join(caminho), "paginas": f"{ch['pagina_inicio']}-{ch['pagina_fim']}",
        "parte": f"{ch.get('parte', 1)} de {ch.get('de_partes', 1)}", "pdf_origem": doc.get("arquivo"),
    }
    fm = "\n".join(f"{k}: {_yaml(v)}" for k, v in meta.items())
    return f"---\n{fm}\n---\n\n{cabecalho(perfil, doc, ch)}\n\n{ch['texto'].strip()}\n"


def vigente(doc: dict, hoje: date | None = None) -> bool:
    hoje = hoje or date.today()
    ini, fim = data(doc.get("vigencia_inicio")), data(doc.get("vigencia_fim"))
    return bool(ini and ini <= hoje and (fim is None or fim >= hoje))


def escolher_vigentes(docs: list[dict], hoje: date | None = None) -> set[int]:
    """Para cada (tipo, documento), a versão vigente hoje; se houver mais de uma, a de início mais recente."""
    melhores: dict[tuple, dict] = {}
    for d in docs:
        if not vigente(d, hoje):
            continue
        chave = (d.get("tipo"), d.get("documento"))
        atual = melhores.get(chave)
        if atual is None or data(d["vigencia_inicio"]) > data(atual["vigencia_inicio"]):
            melhores[chave] = d
    return {d["id"] for d in melhores.values()}


def pasta_documento(doc: dict) -> str:
    return f"{tx.slug(doc.get('tipo') or 'doc', 20)}/{tx.slug(str(doc.get('documento') or doc['arquivo']), 30)}" \
           f"_v{tx.slug(str(doc.get('versao') or 'sem-versao'), 20)}"


def chunk_id(doc: dict, ch: dict) -> str:
    base = f"{doc['sha256']}|{ch['ordem']}|{ch['texto']}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def exportar(perfil, docs: list[dict], chunks_por_doc: dict[int, list[dict]], relatorios: dict[int, str],
             hoje: date | None = None) -> dict:
    saida = perfil.pasta_saida
    sp, outras = saida / "sharepoint", saida / "outras-versoes"
    # A pasta de saída é gerada por inteiro a cada exportação (nada nela é editado à mão).
    for p in (sp, outras, saida / "relatorios"):
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)
    vig = escolher_vigentes(docs, hoje)
    linhas, n_arquivos = [], 0
    for doc in docs:
        raiz = (sp if doc["id"] in vig else outras) / pasta_documento(doc)
        raiz.mkdir(parents=True, exist_ok=True)
        for ch in chunks_por_doc.get(doc["id"], []):
            nome = f"{ch['ordem']:04d}-{tx.slug(ch['secao'], 50)}.md"
            (raiz / nome).write_text(texto_chunk(perfil, doc, ch), encoding="utf-8")
            n_arquivos += 1
            caminho = ch["caminho"] if isinstance(ch["caminho"], list) else json.loads(ch["caminho"])
            linhas.append({
                "arquivo": str((raiz / nome).relative_to(saida)).replace("\\", "/"), "chunk_id": chunk_id(doc, ch),
                "tipo": doc.get("tipo"), "documento": doc.get("documento"), "titulo": doc.get("titulo"),
                "versao": doc.get("versao"), "vigencia_inicio": doc.get("vigencia_inicio"),
                "vigencia_fim": doc.get("vigencia_fim"), "ato": doc.get("ato"), "secao": ch["secao"],
                "caminho": " > ".join(caminho), "pagina_inicio": ch["pagina_inicio"], "pagina_fim": ch["pagina_fim"],
                "parte": f"{ch.get('parte', 1)}/{ch.get('de_partes', 1)}", "palavras": ch.get("palavras"),
                "pdf_origem": doc.get("arquivo"), "pdf_sha256": doc.get("sha256"),
            })
        if doc["id"] in relatorios:
            (saida / "relatorios" / f"qualidade-{pasta_documento(doc).replace('/', '-')}.md").write_text(
                relatorios[doc["id"]], encoding="utf-8")
    with open(saida / "manifesto.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_MANIFESTO)
        w.writeheader()
        w.writerows(linhas)
    return {"documentos": len(docs), "vigentes": len(vig), "chunks": n_arquivos, "pasta": str(sp)}


def relatorio_markdown(perfil, doc: dict, verificacoes: list[dict]) -> str:
    icone = {"ok": "✅", "info": "ℹ️", "aviso": "⚠️", "falha": "❌"}
    linhas = [f"# Qualidade – {rotulo_documento(perfil, doc)}", "",
              f"- Arquivo: `{doc['arquivo']}` (sha256 {doc['sha256'][:12]}…)",
              f"- Páginas: {doc.get('paginas')}",
              f"- Status: {icone.get(doc.get('status_qualidade'), '')} {doc.get('status_qualidade')}",
              f"- Metadados: {'confirmados automaticamente (conferência cruzada)' if doc.get('metadados_conferencia') == 'automatica' else 'conferidos por uma pessoa'}",
              f"- Aceite: {doc.get('aceito_em') or 'não precisou' if doc.get('status_qualidade') == 'ok' and not doc.get('aceito_em') else doc.get('aceito_em') or 'não'} {('– ' + doc['aceito_nota']) if doc.get('aceito_nota') else ''}",
              "", "| Nível | Página | Verificação | Mensagem |", "| --- | --- | --- | --- |"]
    for v in sorted(verificacoes, key=lambda v: ({"falha": 0, "aviso": 1, "info": 2, "ok": 3}[v["nivel"]], v["pagina"] or 0)):
        if v["nivel"] == "ok" and v["codigo"] == "cobertura":
            continue
        msg = v["mensagem"].replace("|", "\\|")
        linhas.append(f"| {icone[v['nivel']]} | {v['pagina'] or '–'} | {v['codigo']} | {msg} |")
    return "\n".join(linhas) + "\n"
