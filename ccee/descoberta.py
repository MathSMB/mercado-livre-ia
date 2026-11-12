"""Descoberta: lê as páginas oficiais da CCEE e monta o catálogo de documentos.

Regras de Comercialização: a página usa uma API JSON (Liferay) paginada.
Procedimentos de Comercialização: a página é HTML estático com abas por módulo.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from . import config

# ----------------------------------------------------------------- Regras

TIPO_CADERNO = "Caderno de Regras de Comercialização"


def pagina_regras(cliente, numero: int) -> list[dict]:
    p = config.PREFIXO_REGRAS
    dados = {
        p + "search_term": "", p + "filterRule": "0", p + "filterValidity": "0",
        p + "inputInitialDate": "", p + "inputFinalDate": "",
        p + "results": str(config.ITENS_POR_PAGINA), p + "numberPage": str(numero),
        p + "orderBy": "documento", p + "desc": "false",
    }
    resp = cliente.post(config.URL_REGRAS_API, data=dados)
    return resp.json().get("results", [])


def listar_regras_brutas(cliente) -> list[dict]:
    """Pagina até vir uma página vazia. O 'total' da API não é confiável."""
    vistos: dict[int, dict] = {}
    for numero in range(1, config.MAX_PAGINAS + 1):
        itens = pagina_regras(cliente, numero)
        if not itens:
            break
        novos = 0
        for item in itens:
            fid = item.get("fileEntryID")
            if fid is not None and fid not in vistos:
                vistos[fid] = item
                novos += 1
        if novos == 0:  # página repetida: evita laço infinito
            break
    return list(vistos.values())


def _mes(texto: str | None) -> str | None:
    """'05/2026' -> '2026-05'."""
    if not texto:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{4})\s*", texto)
    return f"{m.group(2)}-{int(m.group(1)):02d}" if m else None


def _data_br(texto: str | None) -> str | None:
    """'22/12/2025' -> '2025-12-22'."""
    if not texto:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _limpa_versao(v: str | None) -> str | None:
    return v.strip().rstrip(".") if v else None


def normalizar_regra(item: dict) -> dict:
    nome = item.get("nomeDocumento") or ""
    complementar = item.get("nomeComplementar") or nome
    m = re.match(r"\s*(\d{2})\s*[-–_]", complementar) or re.match(r"\s*(\d{2})\s*[-–_]", nome)
    m_arq = re.search(r"_(\d{4}\.\d+\.\d+(?:\.\d+)?)", nome)
    return {
        "id": f"regras:{item['fileEntryID']}",
        "fonte": "regras",
        "codigo": m.group(1) if m else None,
        "titulo": complementar.strip(),
        "versao": _limpa_versao(item.get("versao")),
        "versao_arquivo": m_arq.group(1) if m_arq else None,
        "vigencia_inicio": _mes(item.get("periodoDe")),
        "vigencia_fim": _mes(item.get("periodoAte")),
        "situacao_site": item.get("categoryJournal"),
        "tipo_documento": item.get("nomeDocumentoList"),
        "grupo": item.get("tipoDocumentalList"),
        "ato_aneel": item.get("instrumentoNome"),
        "ato_url": item.get("instrumentoUrl"),
        "url": (item.get("url") or "").strip(),
        "nome_arquivo": nome,
        "extensao": (item.get("extension") or "").upper() or None,
        "hash_site": item.get("hash"),
        "publicado_em": _data_br(item.get("publishDateFMT")),
    }


def selecionar_regras_vigentes(docs: list[dict], hoje: date | None = None) -> None:
    """Marca 'selecionado' = caderno em PDF cujo período cobre o mês atual.

    Se houver mais de um para o mesmo caderno, fica o publicado por último
    (os outros recebem alerta). Altera os dicionários no lugar."""
    hoje = hoje or date.today()
    mes = f"{hoje.year}-{hoje.month:02d}"
    candidatos: dict[str, list[dict]] = {}
    for d in docs:
        d["selecionado"] = 0
        d["motivo_selecao"] = None
        d["alerta"] = None
        if d["tipo_documento"] != TIPO_CADERNO or d["extensao"] != "PDF" or not d["codigo"]:
            d["motivo_selecao"] = "fora do escopo (não é caderno em PDF)"
            continue
        ini, fim = d["vigencia_inicio"], d["vigencia_fim"]
        if ini and fim and ini <= mes <= fim:
            candidatos.setdefault(d["codigo"], []).append(d)
        else:
            d["motivo_selecao"] = "histórico (fora da vigência atual)"
        if d["versao"] and d["versao_arquivo"] and d["versao"] != d["versao_arquivo"]:
            d["alerta"] = f"Versão no site ({d['versao']}) difere da do arquivo ({d['versao_arquivo']})"
    for codigo, lista in candidatos.items():
        lista.sort(key=lambda x: (x["publicado_em"] or "", x["versao"] or ""), reverse=True)
        escolhido = lista[0]
        escolhido["selecionado"] = 1
        escolhido["motivo_selecao"] = f"vigente em {mes}"
        for outro in lista[1:]:
            outro["motivo_selecao"] = f"período cobre {mes}, mas há publicação mais recente ({escolhido['versao']})"
        if len(lista) > 1:
            msg = f"{len(lista)} versões cobrem {mes}; escolhida {escolhido['versao']} (mais recente)"
            escolhido["alerta"] = "; ".join(filter(None, [escolhido["alerta"], msg]))


def descobrir_regras(cliente, hoje: date | None = None) -> list[dict]:
    docs = [normalizar_regra(i) for i in listar_regras_brutas(cliente) if i.get("fileEntryID")]
    selecionar_regras_vigentes(docs, hoje)
    return docs


# ------------------------------------------------------------------ PdCs

RX_TITULO_PDC = re.compile(
    r"(?:Subm[óo]dulo\s+(?P<cod>\d+\.\d+)\s*[-–]\s*)?(?P<nome>.*?)\s*[-–]\s*Vers[ãa]o\s+(?P<ver>[\dX]+\.\d+)"
    r"\s*[-–]\s*(?P<data>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)
RX_VERSAO_ARQ = re.compile(r"_v(\d+\.\d+)", re.IGNORECASE)

ABAS_FORA_DO_ESCOPO = ("Provisório", "Provisórios", "Futuros")


def _extensao_url(url: str) -> str:
    """Links do Liferay terminam em /<arquivo.ext>/<uuid>; links CCEE_xxx não têm extensão (são PDF)."""
    m = re.search(r"\.(pdf|zip|7z|docx?|xlsx?)(?=/|\?|$)", unquote(url), re.IGNORECASE)
    return m.group(1).upper() if m else "PDF"


def _chave_pdc(titulo: str, url: str) -> str:
    return hashlib.sha1(f"{titulo}|{url}".encode()).hexdigest()[:12]


def parse_pdcs(html: str, base: str = config.URL_PDCS) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    nomes_abas = {}
    for a in soup.select('a.nav-link[href^="#v-pills"], [data-bs-target^="#v-pills"], [data-target^="#v-pills"]'):
        alvo = a.get("href") or a.get("data-bs-target") or a.get("data-target")
        nomes_abas[alvo.lstrip("#")] = a.get_text(" ", strip=True)

    docs = []
    for aba in soup.select(".tab-pane"):
        nome_aba = nomes_abas.get(aba.get("id"), aba.get("id"))
        for dt in aba.select("dl > dt"):
            titulo = dt.get_text(" ", strip=True)
            dd = dt.find_next_sibling("dd")
            if dd is None:
                continue
            link = next((a for a in dd.find_all("a") if "Acesse o PdC" in a.get_text()), None)
            if link is None or not link.get("href"):
                continue
            url = urljoin(base, link["href"].strip())
            m = RX_TITULO_PDC.search(titulo)
            m_arq = RX_VERSAO_ARQ.search(unquote(url))
            fora = any(p in nome_aba for p in ABAS_FORA_DO_ESCOPO)
            d = {
                "id": f"pdc:{(m.group('cod') if m and m.group('cod') else _chave_pdc(titulo, url))}"
                + (":prov" if fora else ""),
                "fonte": "pdc",
                "codigo": m.group("cod") if m else None,
                "titulo": titulo,
                "versao": m.group("ver") if m else None,
                "versao_arquivo": m_arq.group(1) if m_arq else None,
                "vigencia_inicio": _data_br(m.group("data")) if m else None,
                "vigencia_fim": None,
                "situacao_site": nome_aba,
                "tipo_documento": "Procedimento de Comercialização",
                "grupo": nome_aba,
                "ato_aneel": None,
                "ato_url": None,
                "url": url,
                "nome_arquivo": None,
                "extensao": _extensao_url(url),
                "hash_site": None,
                "publicado_em": None,
                "selecionado": 0 if fora else 1,
                "motivo_selecao": f"aba '{nome_aba}' fica para depois" if fora else "submódulo vigente",
                "alerta": None,
            }
            if d["versao"] and d["versao_arquivo"] and d["versao"] != d["versao_arquivo"]:
                d["alerta"] = (
                    f"Título diz versão {d['versao']}, mas o link aponta para o arquivo v{d['versao_arquivo']}"
                )
            if fora and d["codigo"] and "Futuros" in nome_aba:
                d["id"] = f"pdc:{d['codigo']}:futuro"
            docs.append(d)
    # Evita IDs repetidos (ex.: um provisório sem número de submódulo)
    vistos = {}
    for d in docs:
        if d["id"] in vistos:
            d["id"] = f"{d['id']}:{_chave_pdc(d['titulo'], d['url'])}"
        vistos[d["id"]] = True
    return docs


def descobrir_pdcs(cliente) -> list[dict]:
    return parse_pdcs(cliente.get(config.URL_PDCS).text)
