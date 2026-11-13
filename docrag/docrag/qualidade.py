"""Portão de qualidade: compara o PDF, a conversão principal e a de verificação.

Três leituras independentes do mesmo documento:
  1. a camada de texto do próprio PDF (o que está escrito, letra por letra);
  2. o conversor principal (o texto que vai para a base);
  3. o conversor de verificação.
Cada verificação devolve ok, aviso (alguém precisa olhar) ou falha (não pode ir para a base
sem correção ou aceite registrado).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from . import texto as tx
from .conversores import Conversao, InfoPdf

OK, AVISO, FALHA = "ok", "aviso", "falha"
ORDEM = {OK: 0, AVISO: 1, FALHA: 2}
RE_TITULO_NUMERADO = re.compile(r"^#{1,6}\s*\**\s*(\d+(?:\.\d+)*)\.?\s+\S", re.M)


@dataclass
class Verificacao:
    codigo: str
    nivel: str
    mensagem: str
    pagina: int | None = None
    detalhe: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Relatorio:
    verificacoes: list[Verificacao]

    @property
    def status(self) -> str:
        if not self.verificacoes:
            return OK
        return max((v.nivel for v in self.verificacoes), key=ORDEM.get)

    def contagem(self) -> dict[str, int]:
        c = Counter(v.nivel for v in self.verificacoes)
        return {OK: c[OK], AVISO: c[AVISO], FALHA: c[FALHA]}

    def problemas(self) -> list[Verificacao]:
        return [v for v in self.verificacoes if v.nivel != OK]


def _nivel(valor: float, aviso: float, falha: float) -> str:
    return FALHA if valor < falha else AVISO if valor < aviso else OK


def numeros_titulos(md: str) -> list[str]:
    return RE_TITULO_NUMERADO.findall(md)


def saltos_numeracao(numeros: list[str]) -> list[str]:
    """Aponta saltos como 3.1 -> 3.3 (indício de seção perdida ou título não reconhecido)."""
    saltos, vistos = [], set()
    anterior: list[int] | None = None
    for n in numeros:
        partes = [int(x) for x in n.split(".")]
        if anterior is not None and len(partes) == len(anterior) and partes[:-1] == anterior[:-1]:
            if partes[-1] > anterior[-1] + 1:
                saltos.append(f"{'.'.join(map(str, anterior))} → {n}")
        elif anterior is not None and len(partes) == 1 and partes[0] > anterior[0] + 1:
            saltos.append(f"{anterior[0]} → {n}")
        if n not in vistos:
            vistos.add(n)
            anterior = partes
    return saltos


def avaliar(info: InfoPdf, principal: Conversao, verificador: Conversao | None, limiares,
            final_md: str | None = None) -> Relatorio:
    """`final_md` é o texto que vai para a base (o principal, ou a versão corrigida à mão)."""
    v: list[Verificacao] = []
    final_pag = principal.paginas
    if final_md is not None:
        from .conversores import separar_paginas
        final_pag = separar_paginas(final_md)

    # 1. Número de páginas
    for conv in filter(None, [principal, verificador]):
        if len(conv.paginas) != info.n_paginas:
            v.append(Verificacao("paginas", FALHA, f"{conv.conversor} leu {len(conv.paginas)} páginas; o PDF tem "
                                 f"{info.n_paginas}.", detalhe={"conversor": conv.conversor}))
    if len(final_pag) != info.n_paginas:
        v.append(Verificacao("paginas", FALHA, f"O texto final tem {len(final_pag)} páginas; o PDF tem {info.n_paginas}. "
                             "Não apague as marcas <!-- página N --> ao corrigir."))
    if not any(c.codigo == "paginas" for c in v):
        v.append(Verificacao("paginas", OK, f"{info.n_paginas} páginas em todas as leituras."))

    repetidas = tx.linhas_repetidas([tx.texto_pdf(t) for t in info.textos])
    logo = sum(1 for n in info.imagens if n) >= 0.8 * max(info.n_paginas, 1) and info.n_paginas >= 3
    n_formulas = {}

    for i in range(info.n_paginas):
        pg = i + 1
        ref = tx.palavras(tx.sem_linhas(tx.texto_pdf(info.textos[i]), repetidas))
        md_final = final_pag[i] if i < len(final_pag) else ""
        md_ver = verificador.paginas[i] if verificador and i < len(verificador.paginas) else None
        pal_final = tx.palavras(tx.markdown_para_texto(md_final))
        imagens = max(0, info.imagens[i] - (1 if logo else 0))

        # 2. Página sem camada de texto (escaneada) ou quase só figura
        if len(ref) < limiares.min_palavras_pagina:
            if imagens:
                v.append(Verificacao("figura", AVISO, "Página quase só com imagem (fluxograma/figura?). O conteúdo da "
                                     "figura não vira texto: descreva-a à mão no texto final ou aceite.", pg,
                                     {"imagens": imagens, "palavras_pdf": len(ref)}))
            elif not ref and pal_final:
                v.append(Verificacao("sem_texto_pdf", AVISO, "O PDF não tem texto nesta página, mas a conversão tem. "
                                     "Confira à mão (pode ser OCR).", pg))
            continue
        if imagens:
            v.append(Verificacao("figura", AVISO, f"Página com {imagens} imagem(ns). Se for fluxograma ou tabela em "
                                 "imagem, o conteúdo não está no texto.", pg, {"imagens": imagens}))

        # 3. Cobertura: tudo o que está escrito no PDF aparece no texto final?
        cob, faltando = tx.cobertura(ref, pal_final)
        nivel = _nivel(cob, limiares.cobertura_aviso, limiares.cobertura_falha)
        v.append(Verificacao("cobertura", nivel, f"{cob:.1%} das palavras do PDF estão no texto final.", pg,
                             {"cobertura": round(cob, 4), "faltando": faltando[:25]}))

        # 4. Números: prazos, percentuais, artigos, valores. Um número a menos é erro grave.
        nums_pdf = Counter(tx.numeros(tx.sem_linhas(tx.texto_pdf(info.textos[i]), repetidas)))
        nums_final = Counter(tx.numeros(tx.markdown_para_texto(md_final)))
        falta_num = sorted((nums_pdf - nums_final).elements())
        sobra_num = sorted((nums_final - nums_pdf).elements())
        if falta_num or sobra_num:
            v.append(Verificacao("numeros", AVISO if len(falta_num) <= 2 and not sobra_num else FALHA,
                                 f"Números diferentes do PDF: faltando {falta_num[:15]}, a mais {sobra_num[:15]}.", pg,
                                 {"faltando": falta_num, "a_mais": sobra_num}))

        if md_ver is None:
            continue
        pal_ver = tx.palavras(tx.markdown_para_texto(md_ver))

        # 5. Concordância entre os dois conversores
        sm = SequenceMatcher(None, pal_final, pal_ver, autojunk=False)
        conc = sm.ratio() if (pal_final or pal_ver) else 1.0
        nivel = _nivel(conc, limiares.concordancia_aviso, limiares.concordancia_falha)
        if nivel != OK:
            trechos = [(" ".join(pal_final[a1:a2][:20]), " ".join(pal_ver[b1:b2][:20]))
                       for op, a1, a2, b1, b2 in sm.get_opcodes() if op != "equal"][:8]
            v.append(Verificacao("concordancia", nivel, f"Os conversores concordam em {conc:.1%} do texto.", pg,
                                 {"concordancia": round(conc, 4), "diferencas": trechos}))

        # 6. Tabelas
        t_final, t_ver = tx.contar_tabelas(md_final), tx.contar_tabelas(md_ver)
        if t_final != t_ver:
            v.append(Verificacao("tabelas", AVISO, f"Tabelas: texto final tem {t_final}, {verificador.conversor} achou "
                                 f"{t_ver}. Confira se alguma tabela virou texto solto ou se partiu.", pg,
                                 {"final": t_final, "verificador": t_ver}))

        # 7. Fórmulas
        bf, _ = tx.formulas(md_final)
        bv, _ = tx.formulas(md_ver)
        n_formulas[pg] = len(bf)
        if len(bf) != len(bv):
            v.append(Verificacao("formulas", AVISO, f"Fórmulas em bloco: texto final tem {len(bf)}, "
                                 f"{verificador.conversor} achou {len(bv)}.", pg,
                                 {"final": bf, "verificador": bv}))
        else:
            diferentes = [(a, b) for a, b in zip(bf, bv) if _latex_chave(a) != _latex_chave(b)]
            if diferentes:
                v.append(Verificacao("formulas", AVISO, f"{len(diferentes)} fórmula(s) escritas de forma diferente pelos "
                                     "dois conversores. Confira com o PDF.", pg, {"pares": diferentes}))

    # 8. Títulos numerados: as duas leituras acham as mesmas seções?
    md_final_todo = "\n".join(final_pag)
    tit_final = numeros_titulos(md_final_todo)
    if verificador:
        tit_ver = numeros_titulos(verificador.markdown)
        so_final, so_ver = sorted(set(tit_final) - set(tit_ver)), sorted(set(tit_ver) - set(tit_final))
        if so_final or so_ver:
            v.append(Verificacao("titulos", AVISO, "Seções numeradas diferentes entre os conversores: "
                                 f"só no texto final {so_final[:15]}, só no {verificador.conversor} {so_ver[:15]}.",
                                 detalhe={"so_final": so_final, "so_verificador": so_ver}))
    saltos = saltos_numeracao(tit_final)
    if saltos:
        v.append(Verificacao("titulos", AVISO, f"Saltos na numeração das seções: {saltos[:10]}. Pode haver título "
                             "não reconhecido (os chunks dessa parte ficariam com a seção errada).",
                             detalhe={"saltos": saltos}))
    if not tit_final:
        v.append(Verificacao("titulos", AVISO, "Nenhum título numerado encontrado; os chunks serão cortados só pelos "
                             "títulos Markdown."))
    return Relatorio(v)


def _latex_chave(latex: str) -> str:
    t = re.sub(r"\\(left|right|,|;|!|quad|qquad)", "", latex)
    return re.sub(r"[\s{}]", "", t)
