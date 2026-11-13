"""Glossário de siglas e variáveis.

Duas fontes:
  - automática: tirada literalmente do texto convertido, sem inventar nada:
      * "Nome Por Extenso (SIGLA)" quando as iniciais batem com a sigla;
      * tabelas de duas colunas cujo cabeçalho fala em sigla/variável/termo e descrição/definição;
  - manual: `glossario.csv` do perfil (vence a automática para o mesmo termo).
O resultado vira `glossario.md` dentro da pasta do SharePoint, para o Copilot achar as siglas.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from . import texto as tx

LIGACOES = {"de", "da", "do", "das", "dos", "e", "para", "a", "o", "em", "na", "no", "por", "com", "ao", "à"}
RE_SIGLA_PARENTESES = re.compile(r"([A-ZÀ-Ý][\w\-]*(?:\s+[\w\-]+){0,14})\s*\(\s*([A-Z][A-Z0-9]{1,11})\s*\)")
CAB_TERMO = re.compile(r"sigla|vari[aá]vel|termo|par[aâ]metro|s[ií]mbolo", re.I)
CAB_DESC = re.compile(r"descri[cç][aã]o|defini[cç][aã]o|significado", re.I)
COLUNAS = ["termo", "tipo", "definicao", "unidade", "fonte", "origem"]


@dataclass
class Entrada:
    termo: str
    tipo: str          # sigla | variavel | termo
    definicao: str
    unidade: str = ""
    fonte: str = ""    # documento e seção de onde saiu
    origem: str = "automatica"


def _expansao(palavras_antes: list[str], sigla: str) -> str | None:
    """Volta pelas palavras até as iniciais (sem palavras de ligação) formarem a sigla."""
    letras = sigla.lower()
    escolhidas: list[str] = []
    iniciais = ""
    for p in reversed(palavras_antes):
        escolhidas.insert(0, p)
        if p.lower() in LIGACOES:
            continue
        iniciais = p[0].lower() + iniciais
        if iniciais == letras:
            while escolhidas and escolhidas[0].lower() in LIGACOES:
                escolhidas.pop(0)
            return " ".join(escolhidas)
        if not letras.endswith(iniciais):
            return None
    return None


def siglas_do_texto(md: str, fonte: str) -> list[Entrada]:
    achadas = {}
    texto = tx.RE_COMENTARIO.sub(" ", md)
    for m in RE_SIGLA_PARENTESES.finditer(texto):
        palavras = m.group(1).split()
        sigla = m.group(2)
        exp = _expansao(palavras, sigla)
        if exp and sigla not in achadas:
            achadas[sigla] = Entrada(sigla, "sigla", exp, fonte=fonte)
    return list(achadas.values())


def _celulas(linha: str) -> list[str]:
    return [c.strip() for c in linha.strip().strip("|").split("|")]


def tabelas_do_texto(md: str, fonte: str) -> list[Entrada]:
    entradas = []
    linhas = md.splitlines()
    i = 0
    while i < len(linhas) - 1:
        if linhas[i].strip().startswith("|") and tx.RE_SEPARADOR_TABELA.match(linhas[i + 1]):
            cab = _celulas(linhas[i])
            col_t = next((k for k, c in enumerate(cab) if CAB_TERMO.search(c)), None)
            col_d = next((k for k, c in enumerate(cab) if CAB_DESC.search(c)), None)
            col_u = next((k for k, c in enumerate(cab) if re.search(r"unidade", c, re.I)), None)
            j = i + 2
            while j < len(linhas) and linhas[j].strip().startswith("|"):
                if col_t is not None and col_d is not None:
                    cel = _celulas(linhas[j])
                    if len(cel) > max(col_t, col_d) and cel[col_t] and cel[col_d]:
                        termo = cel[col_t].replace("\\_", "_")
                        tipo = "sigla" if re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", termo) and re.search(r"sigla", cab[col_t], re.I) \
                            else "variavel"
                        un = cel[col_u] if col_u is not None and col_u < len(cel) else ""
                        entradas.append(Entrada(termo, tipo, cel[col_d], un, fonte))
                j += 1
            i = j
        else:
            i += 1
    return entradas


def ler_manual(caminho: Path) -> list[Entrada]:
    if not caminho.exists():
        return []
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return [Entrada(l["termo"].strip(), (l.get("tipo") or "termo").strip(), l["definicao"].strip(),
                        (l.get("unidade") or "").strip(), (l.get("fonte") or "").strip(), "manual")
                for l in csv.DictReader(f) if (l.get("termo") or "").strip() and (l.get("definicao") or "").strip()]


def montar(perfil, documentos: list[tuple[str, str]]) -> list[Entrada]:
    """documentos = [(rótulo da fonte, markdown final)]."""
    auto: dict[tuple[str, str], Entrada] = {}
    for fonte, md in documentos:
        for e in siglas_do_texto(md, fonte) + tabelas_do_texto(md, fonte):
            chave = (e.termo, e.tipo)
            if chave in auto:
                if fonte not in auto[chave].fonte:
                    auto[chave].fonte += f"; {fonte}"
            else:
                auto[chave] = e
    manual = {e.termo: e for e in ler_manual(perfil.arquivo("glossario.csv"))}
    final = [e for e in auto.values() if e.termo not in manual] + list(manual.values())
    return sorted(final, key=lambda e: (e.tipo, e.termo.lower()))


def escrever(entradas: list[Entrada], md_destino: Path, csv_destino: Path, titulo: str):
    md_destino.parent.mkdir(parents=True, exist_ok=True)
    nomes = {"sigla": "Siglas", "variavel": "Variáveis das fórmulas", "termo": "Termos"}
    partes = [f"# Glossário – {titulo}", "",
              "Siglas, variáveis e termos usados nos documentos. Entradas automáticas foram copiadas do próprio "
              "texto do documento indicado em *fonte*.", ""]
    for tipo in ["sigla", "variavel", "termo"]:
        grupo = [e for e in entradas if e.tipo == tipo]
        if not grupo:
            continue
        partes += [f"## {nomes[tipo]}", ""]
        for e in grupo:
            un = f" Unidade: {e.unidade}." if e.unidade else ""
            fonte = f" (fonte: {e.fonte})" if e.fonte else ""
            partes.append(f"- **{e.termo}**: {e.definicao}.{un}{fonte}".replace("..", "."))
        partes.append("")
    md_destino.write_text("\n".join(partes), encoding="utf-8")
    with open(csv_destino, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        w.writeheader()
        for e in entradas:
            w.writerow({c: getattr(e, c) for c in COLUNAS})
