"""Quebra o Markdown final em chunks por seção.

Regras:
- a unidade é a seção numerada (até `nivel_maximo`, ex. 3.2.1); subseções mais profundas
  ficam dentro da mãe;
- um chunk nunca corta uma tabela, uma fórmula ou um parágrafo no meio; seção grande é
  dividida entre parágrafos, e tabela grande é dividida por linhas repetindo o cabeçalho;
- seção muito curta (ou só o título) é juntada com a seguinte do mesmo ramo;
- cada chunk sabe o caminho completo de títulos e as páginas de onde veio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import texto as tx
from .conversores import RE_MARCA_PAGINA

RE_TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RE_NUMERO = re.compile(r"^\**\s*(\d+(?:\.\d+)*)\.?\s+(.+?)\**$")


@dataclass
class Bloco:
    texto: str
    pagina: int
    tipo: str = "texto"     # texto | tabela | formula | titulo


@dataclass
class Secao:
    numero: str | None
    titulo: str
    nivel: int
    caminho: list[str]
    blocos: list[Bloco] = field(default_factory=list)

    @property
    def rotulo(self) -> str:
        return f"{self.numero} {self.titulo}" if self.numero else self.titulo

    def palavras(self) -> int:
        return sum(len(tx.palavras(tx.markdown_para_texto(b.texto))) for b in self.blocos if b.tipo != "titulo")


@dataclass
class Chunk:
    ordem: int
    secao: str               # rótulo da(s) seção(ões) do chunk
    numero: str | None       # número da primeira seção (ex. 3.2)
    caminho: list[str]       # títulos do mais geral ao mais específico
    pagina_inicio: int
    pagina_fim: int
    texto: str
    parte: int = 1
    de_partes: int = 1

    @property
    def palavras(self) -> int:
        return len(tx.palavras(tx.markdown_para_texto(self.texto)))


def _blocos(md: str) -> list[Bloco]:
    """Divide em blocos indivisíveis, acompanhando a página pelas marcas <!-- página N -->."""
    blocos: list[Bloco] = []
    pagina = 1
    atual: list[str] = []
    tipo_atual = "texto"
    em_formula = False

    def fechar():
        nonlocal atual, tipo_atual
        if atual and any(l.strip() for l in atual):
            blocos.append(Bloco("\n".join(atual).strip("\n"), pagina, tipo_atual))
        atual, tipo_atual = [], "texto"

    for linha in md.splitlines():
        m = RE_MARCA_PAGINA.match(linha.strip())
        if m and not em_formula:
            if tipo_atual != "tabela":   # tabela que atravessa a página continua o mesmo bloco
                fechar()
            pagina = int(m.group(1))
            continue
        s = linha.strip()
        if em_formula:
            atual.append(linha)
            if s.endswith("$$"):
                em_formula = False
                fechar()
            continue
        if s.startswith("$$"):
            fechar()
            atual, tipo_atual = [linha], "formula"
            if not (s.endswith("$$") and len(s) > 2):
                em_formula = True
            else:
                fechar()
            continue
        if RE_TITULO.match(s):
            fechar()
            blocos.append(Bloco(s, pagina, "titulo"))
            continue
        if s.startswith("|"):
            if tipo_atual != "tabela":
                fechar()
                tipo_atual = "tabela"
            atual.append(linha)
            continue
        if not s:
            if tipo_atual != "tabela":
                fechar()
            continue
        if tipo_atual == "tabela":
            fechar()
        atual.append(linha)
    fechar()
    return blocos


def _titulo(s: str) -> tuple[int, str | None, str]:
    m = RE_TITULO.match(s)
    hashes, texto = len(m.group(1)), m.group(2).strip().strip("*").strip()
    n = RE_NUMERO.match(texto)
    if n:
        return len(n.group(1).split(".")), n.group(1), n.group(2).strip().strip("*").strip()
    return hashes if hashes > 1 else 1, None, texto


def secoes(md: str, nivel_maximo: int = 3) -> list[Secao]:
    lista: list[Secao] = []
    pilha: list[tuple[int, str]] = []
    atual = Secao(None, "Início do documento", 0, [])
    for b in _blocos(md):
        if b.tipo == "titulo":
            nivel, numero, titulo = _titulo(b.texto)
            # títulos sem número abaixo de um numerado (ex. "Notas") ficam dentro da seção atual
            if numero is None and pilha and nivel > 1 and atual.numero:
                atual.blocos.append(b)
                continue
            if nivel > nivel_maximo:
                atual.blocos.append(b)
                continue
            if numero is None:
                nivel = 1 if not pilha else min(nivel, pilha[-1][0] + 1)
            while pilha and pilha[-1][0] >= nivel:
                pilha.pop()
            rotulo = f"{numero} {titulo}" if numero else titulo
            pilha.append((nivel, rotulo))
            lista.append(atual)
            atual = Secao(numero, titulo, nivel, [r for _, r in pilha], [b])
        else:
            atual.blocos.append(b)
    lista.append(atual)
    return [s for s in lista if s.blocos]


def _dividir_tabela(bloco: Bloco, max_palavras: int) -> list[Bloco]:
    linhas = bloco.texto.splitlines()
    if len(linhas) < 4:
        return [bloco]
    cab, corpo = linhas[:2], linhas[2:]
    partes, atual, n = [], [], 0
    for l in corpo:
        p = len(tx.palavras(tx.markdown_para_texto(l)))
        if atual and n + p > max_palavras:
            partes.append(Bloco("\n".join(cab + atual), bloco.pagina, "tabela"))
            atual, n = [], 0
        atual.append(l)
        n += p
    if atual:
        partes.append(Bloco("\n".join(cab + atual), bloco.pagina, "tabela"))
    return partes


def _empacotar(blocos: list[Bloco], max_palavras: int) -> list[list[Bloco]]:
    grupos, atual, n = [], [], 0
    for b in blocos:
        pedacos = _dividir_tabela(b, max_palavras) if b.tipo == "tabela" else [b]
        for p in pedacos:
            w = len(tx.palavras(tx.markdown_para_texto(p.texto)))
            if atual and n + w > max_palavras and any(x.tipo != "titulo" for x in atual):
                grupos.append(atual)
                atual, n = [], 0
            atual.append(p)
            n += w
    if atual:
        grupos.append(atual)
    return grupos


def _mesmo_ramo(a: Secao, b: Secao) -> bool:
    if not a.caminho or not b.caminho:
        return not a.caminho and not b.caminho
    return b.caminho[: len(a.caminho)] == a.caminho or a.caminho[:-1] == b.caminho[:-1]


def chunks(md: str, max_palavras: int = 700, min_palavras: int = 60, nivel_maximo: int = 3) -> list[Chunk]:
    # 1. junta seções curtas (ou só com título) com a seguinte do mesmo ramo
    finais: list[list[Secao]] = []
    grupo: list[Secao] = []
    for s in secoes(md, nivel_maximo):
        if grupo and not _mesmo_ramo(grupo[-1], s):
            finais.append(grupo)
            grupo = []
        grupo.append(s)
        if sum(x.palavras() for x in grupo) >= min_palavras:
            finais.append(grupo)
            grupo = []
    if grupo:
        if finais and _mesmo_ramo(finais[-1][-1], grupo[0]) and sum(x.palavras() for x in grupo) > 0:
            finais[-1].extend(grupo)
        else:
            finais.append(grupo)

    # 2. corta unidades grandes entre blocos
    resultado: list[Chunk] = []
    for grupo in finais:
        com_texto = [s for s in grupo if s.palavras() > 0] or grupo
        principal = com_texto[0]
        rotulo = " | ".join(s.rotulo for s in com_texto)
        blocos = [b for s in grupo for b in s.blocos]
        pacotes = _empacotar(blocos, max_palavras)
        for i, pac in enumerate(pacotes, start=1):
            resultado.append(Chunk(
                ordem=len(resultado) + 1, secao=rotulo, numero=principal.numero,
                caminho=principal.caminho or [principal.titulo],
                pagina_inicio=min(b.pagina for b in pac), pagina_fim=max(b.pagina for b in pac),
                texto="\n\n".join(b.texto for b in pac).strip(), parte=i, de_partes=len(pacotes)))
    return resultado
