"""Normalização de texto usada na comparação e no chunking."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

RE_LATEX_BLOCO = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
RE_LATEX_INLINE = re.compile(r"(?<![\\$])\$(?!\$)([^$\n]+?)(?<!\\)\$(?!\$)")
RE_COMENTARIO = re.compile(r"<!--.*?-->", re.S)
RE_TAG = re.compile(r"<[^>]+>")
RE_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
RE_PALAVRA = re.compile(r"[^\W_]+", re.U)  # "_" separa: V_TOT e V\_TOT viram v, tot
RE_NUMERO = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")  # pega também 10 em "MS+10du"
RE_SEPARADOR_TABELA = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", re.M)
RE_HIFEN_QUEBRA = re.compile(r"(\w)[\-\u00ad]\s*\n\s*(\w)")


def normalizar_unicode(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).replace("°", "o").replace("º", "o")  # 1°d, 1ºd e 1od são o mesmo
    return t.replace("\u00ad", "").replace("\ufffe", "").replace("\x02", "").replace("\r", "\n")


def latex_para_texto(latex: str) -> str:
    """Achata LaTeX para palavras comparáveis com a camada de texto do PDF: V_{TOT} -> VTOT."""
    t = re.sub(r"\\(text|mathrm|mathit|mathbf|operatorname)\s*\{([^}]*)\}", r"\2", latex)
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    t = re.sub(r"[_^{}]", "", t)
    return t


def formulas(md: str) -> tuple[list[str], list[str]]:
    """(fórmulas em bloco, fórmulas em linha)."""
    blocos = [(a or b).strip() for a, b in RE_LATEX_BLOCO.findall(md)]
    sem_blocos = RE_LATEX_BLOCO.sub(" ", md)
    inline = [m.strip() for m in RE_LATEX_INLINE.findall(sem_blocos)]
    return blocos, inline


def markdown_para_texto(md: str) -> str:
    t = normalizar_unicode(md)
    t = RE_COMENTARIO.sub(" ", t)
    t = RE_LATEX_BLOCO.sub(lambda m: " " + latex_para_texto(m.group(1) or m.group(2)) + " ", t)
    t = RE_LATEX_INLINE.sub(lambda m: " " + latex_para_texto(m.group(1)) + " ", t)
    t = RE_LINK.sub(r"\1", t)
    t = RE_TAG.sub(" ", t)
    t = RE_SEPARADOR_TABELA.sub(" ", t)
    t = re.sub(r"[|#*_`>~]", " ", t)
    return t


def texto_pdf(t: str) -> str:
    t = normalizar_unicode(t)
    return RE_HIFEN_QUEBRA.sub(r"\1\2", t)


def palavras(texto: str) -> list[str]:
    return [p.lower() for p in RE_PALAVRA.findall(texto) if len(p) > 1 or p.isdigit()]


def numeros(texto: str) -> list[str]:
    return RE_NUMERO.findall(texto)


def contar_tabelas(md: str) -> int:
    return len(RE_SEPARADOR_TABELA.findall(md)) + len(re.findall(r"<table\b", md, re.I))


def cobertura(referencia: list[str], conversao: list[str]) -> tuple[float, list[str]]:
    """Fração das palavras da referência (com repetição) que aparecem na conversão."""
    ref, conv = Counter(referencia), Counter(conversao)
    total = sum(ref.values())
    if not total:
        return 1.0, []
    achadas = sum(min(c, conv[w]) for w, c in ref.items())
    faltando = [w for w, c in ref.most_common() if conv[w] < c]
    return achadas / total, faltando


def linhas_repetidas(paginas: list[str], fracao: float = 0.5) -> set[str]:
    """Linhas que se repetem em boa parte das páginas: cabeçalho e rodapé (com números trocados por #)."""
    if len(paginas) < 2:
        return set()
    contagem = Counter()
    for p in paginas:
        contagem.update({re.sub(r"\d+", "#", l.strip()) for l in p.splitlines() if l.strip()})
    corte = max(2, int(len(paginas) * fracao))
    return {l for l, c in contagem.items() if c >= corte}


def sem_linhas(texto: str, linhas: set[str]) -> str:
    if not linhas:
        return texto
    return "\n".join(l for l in texto.splitlines() if re.sub(r"\d+", "#", l.strip()) not in linhas)


def slug(t: str, limite: int = 60) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:limite].rstrip("-") or "secao"
