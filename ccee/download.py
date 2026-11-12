"""Download dos arquivos, com verificação de integridade e nomes de arquivo seguros."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from . import config

RX_CD = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)
ASSINATURAS = {"PDF": b"%PDF", "ZIP": b"PK"}


def url_segura(url: str) -> str:
    """Codifica espaços e acentos do caminho (o site publica URLs com espaços)."""
    p = urlsplit(url.strip())
    caminho = quote(unquote(p.path), safe="/%:@")
    return urlunsplit((p.scheme, p.netloc, caminho, p.query, p.fragment))


def nome_seguro(nome: str) -> str:
    nome = unquote(nome).strip().replace("/", "_").replace("\\", "_")
    nome = re.sub(r'[<>:"|?*\x00-\x1f]', "_", nome)
    return nome[:150] or "arquivo"


def nome_do_arquivo(doc: dict, resp) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = RX_CD.search(cd)
    if m:
        return nome_seguro(m.group(1))
    if doc.get("nome_arquivo"):
        return nome_seguro(doc["nome_arquivo"])
    partes = [p for p in urlsplit(doc["url"]).path.split("/") if p]
    candidato = next((p for p in reversed(partes) if "." in p), partes[-1] if partes else "arquivo")
    return nome_seguro(candidato)


def baixar(cliente, doc: dict, destino_base: Path | None = None) -> dict:
    """Baixa um documento e devolve {caminho, sha256, tamanho_bytes, nome_arquivo}.

    Salva em dados/pdf/<fonte>/<codigo>/<sha256[:8]>_<nome>, assim versões
    diferentes nunca se sobrescrevem e o histórico fica no disco."""
    destino_base = destino_base or config.PASTA_PDF
    resp = cliente.get(url_segura(doc["url"]), stream=True)
    conteudo = resp.content
    if not conteudo:
        raise ValueError("Arquivo vazio")
    tipo = (doc.get("extensao") or "").upper()
    esperado = ASSINATURAS.get(tipo)
    if esperado and not conteudo.startswith(esperado):
        ct = resp.headers.get("content-type", "?")
        raise ValueError(f"Conteúdo não parece {tipo} (content-type: {ct})")
    sha = hashlib.sha256(conteudo).hexdigest()
    nome = nome_do_arquivo(doc, resp)
    pasta = Path(destino_base) / doc["fonte"] / (doc.get("codigo") or "outros")
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{sha[:8]}_{nome}"
    if not caminho.exists():
        tmp = caminho.with_suffix(caminho.suffix + ".parcial")
        tmp.write_bytes(conteudo)
        tmp.replace(caminho)  # gravação atômica: nunca fica arquivo pela metade
    return {"caminho": str(caminho), "sha256": sha, "tamanho_bytes": len(conteudo), "nome_arquivo": nome}
