"""Árbitro página a página: monta o texto final com a melhor leitura de cada página.

Sem IA: o juiz é a camada de texto do próprio PDF. Para cada página:
  - se a leitura do conversor principal bate com o PDF (todas as palavras e todos os números), fica ela;
  - senão, se outra leitura bate com o PDF e não perde tabela nem fórmula, a página é trocada por ela;
  - se a principal bate com o PDF mas achatou uma tabela que a outra leitura manteve, também troca;
  - senão, fica a principal, e o portão aponta o problema.
Assim um conversor cobre o ponto fraco do outro (ex.: o Docling não extrai o texto de fluxogramas
desenhados e o Marker extrai), e só sobra para uma pessoa o que nenhum dos dois leu certo.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from . import texto as tx
from .conversores import Conversao, InfoPdf


RE_ANCORA = re.compile(r'<span id="[^"]*"></span>\n*')  # âncoras do Marker: não são texto


def limpar(md: str) -> str:
    return RE_ANCORA.sub("", md).strip("\n")


@dataclass
class Leitura:
    conversor: str
    cobertura: float
    numeros_diferentes: int
    tabelas: int
    formulas: int
    palavras_tabela: int

    def confere(self, limiares) -> bool:
        return self.cobertura >= limiares.cobertura_aviso and self.numeros_diferentes == 0


@dataclass
class Escolha:
    pagina: int
    conversor: str
    motivo: str


def ler_pagina(conversor: str, texto_pdf: str, repetidas: set[str], md: str) -> Leitura:
    limpo = tx.sem_linhas(tx.texto_pdf(texto_pdf), repetidas)
    cob, _ = tx.cobertura(tx.palavras(limpo), tx.palavras(tx.markdown_para_texto(md)))
    nums_pdf, nums_md = Counter(tx.numeros(limpo)), Counter(tx.numeros(tx.markdown_para_texto(md)))
    dif = sum(((nums_pdf - nums_md) + (nums_md - nums_pdf)).values())
    return Leitura(conversor, cob, dif, tx.contar_tabelas(md), len(tx.formulas(md)[0]),
                   tx.palavras_em_tabelas(md))


def arbitrar(info: InfoPdf, principal: Conversao, outras: list[Conversao], limiares) -> tuple[list[str], list[Escolha]]:
    """Devolve as páginas do texto final e, para cada página trocada, de onde veio e por quê."""
    repetidas = tx.linhas_repetidas([tx.texto_pdf(t) for t in info.textos])
    paginas, trocas = [], []
    for i in range(info.n_paginas):
        md_p = principal.paginas[i] if i < len(principal.paginas) else ""
        lp = ler_pagina(principal.conversor, info.textos[i], repetidas, md_p)
        escolhido = md_p
        for outra in outras if lp.confere(limiares) else []:
            if len(outra.paginas) != info.n_paginas:
                continue
            lo = ler_pagina(outra.conversor, info.textos[i], repetidas, outra.paginas[i])
            if lo.confere(limiares) and lp.palavras_tabela < 0.9 * lo.palavras_tabela and lo.formulas >= lp.formulas:
                escolhido = limpar(outra.paginas[i])
                trocas.append(Escolha(i + 1, outra.conversor, f"as duas leituras batem com o PDF, mas a do "
                                      f"{principal.conversor} perdeu a forma de tabela e a do {outra.conversor} manteve."))
                break
        if escolhido == md_p and not lp.confere(limiares):
            for outra in outras:
                if len(outra.paginas) != info.n_paginas:
                    continue  # leitura com páginas desalinhadas não serve para trocar página
                lo = ler_pagina(outra.conversor, info.textos[i], repetidas, outra.paginas[i])
                if lo.confere(limiares) and lo.palavras_tabela >= 0.9 * lp.palavras_tabela \
                        and lo.formulas >= lp.formulas:
                    escolhido = limpar(outra.paginas[i])
                    trocas.append(Escolha(i + 1, outra.conversor,
                                          f"a leitura do {principal.conversor} tinha {lp.cobertura:.0%} das palavras do PDF "
                                          f"e {lp.numeros_diferentes} número(s) diferente(s); a do {outra.conversor} "
                                          f"tem {lo.cobertura:.0%} e todos os números."))
                    break
        paginas.append(escolhido)
    return paginas, trocas
