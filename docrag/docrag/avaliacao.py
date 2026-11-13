"""Perguntas de teste: a base contém a resposta, e a busca acha o trecho certo?

`perguntas.csv` do perfil, uma pergunta por linha:
  id, pergunta, resposta_esperada, documento, secao_esperada, trechos
`trechos` são frases copiadas do documento que precisam estar no chunk que responde
(separe várias com " || "). Sem API paga, a checagem é local:
  1. algum chunk tem todos os trechos?  (senão a conversão perdeu a resposta)
  2. uma busca por palavras (BM25) coloca esse chunk entre os primeiros?  (senão o chunk está
     mal rotulado ou grande demais, e o Copilot também tende a não achá-lo)
A resposta do próprio Copilot é registrada à mão em `resultados_copilot.csv` (painel).
"""
from __future__ import annotations

import csv
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import texto as tx

PARADAS = set("""a o as os de da do das dos e é em no na nos nas um uma uns umas para por com sem ao aos à às
que qual quais quando como onde se ser são foi pelo pela pelos pelas ou mais menos deve devem cada este esta
esse essa isso seu sua seus suas""".split())

COLUNAS_PERGUNTAS = ["id", "pergunta", "resposta_esperada", "documento", "secao_esperada", "trechos"]
COLUNAS_COPILOT = ["id", "pergunta", "resposta_esperada", "resposta_copilot", "citou_fonte_certa", "correta",
                   "observacao", "testado_em"]


@dataclass
class Pergunta:
    id: str
    pergunta: str
    resposta_esperada: str
    documento: str
    secao_esperada: str
    trechos: list[str]


@dataclass
class Resultado:
    pergunta: Pergunta
    resultado: str          # achou | fora_do_top | trecho_ausente | sem_trechos
    posicao: int | None
    chunk: str | None
    detalhe: str = ""


def ler_perguntas(caminho: Path) -> list[Pergunta]:
    if not caminho.exists():
        return []
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return [Pergunta(l["id"].strip(), l["pergunta"].strip(), (l.get("resposta_esperada") or "").strip(),
                         (l.get("documento") or "").strip(), (l.get("secao_esperada") or "").strip(),
                         [t.strip() for t in (l.get("trechos") or "").split("||") if t.strip()])
                for l in csv.DictReader(f) if (l.get("id") or "").strip() and not l["id"].startswith("#")]


def _radical(p: str) -> str:
    """Radical bem simples (plural): prazos -> prazo, atividades -> atividade."""
    if len(p) > 4 and p.endswith("ões"):
        return p[:-3] + "ão"
    if len(p) > 3 and p.endswith("s") and not p.isdigit():
        return p[:-1]
    return p


def _termos(t: str) -> list[str]:
    return [_radical(p) for p in tx.palavras(tx.markdown_para_texto(t)) if p not in PARADAS]


class BM25:
    def __init__(self, documentos: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(_termos(d)) for d in documentos]
        self.tam = [sum(d.values()) for d in self.docs]
        self.media = sum(self.tam) / max(len(self.tam), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def ranking(self, consulta: str) -> list[int]:
        q = _termos(consulta)
        notas = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                if t in d:
                    f = d[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.tam[i] / self.media))
            notas.append(s)
        return sorted(range(len(notas)), key=lambda i: -notas[i])


def _norm(t: str) -> str:
    return " ".join(tx.palavras(tx.markdown_para_texto(t)))


def avaliar(perguntas: list[Pergunta], chunks: list[dict], top_k: int = 5) -> list[Resultado]:
    """chunks = [{"id": ..., "texto": texto completo com o cabeçalho de contexto}]."""
    indice = BM25([c["texto"] for c in chunks])
    normalizados = [_norm(c["texto"]) for c in chunks]
    resultados = []
    for p in perguntas:
        if not p.trechos:
            resultados.append(Resultado(p, "sem_trechos", None, None, "Preencha a coluna 'trechos' para testar."))
            continue
        alvos = {i for i, n in enumerate(normalizados) if all(_norm(t) in n for t in p.trechos)}
        if not alvos:
            resultados.append(Resultado(p, "trecho_ausente", None, None,
                                        "Nenhum chunk contém todos os trechos. A conversão pode ter perdido o texto, "
                                        "ou o trecho atravessa dois chunks."))
            continue
        ordem = indice.ranking(p.pergunta)
        pos = next(k for k, i in enumerate(ordem, start=1) if i in alvos)
        alvo = ordem[pos - 1]
        resultados.append(Resultado(p, "achou" if pos <= top_k else "fora_do_top", pos, chunks[alvo]["id"]))
    return resultados


def preparar_planilha_copilot(perguntas: list[Pergunta], caminho: Path):
    """Cria/completa resultados_copilot.csv sem apagar o que já foi preenchido."""
    existentes = {}
    if caminho.exists():
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            existentes = {l["id"]: l for l in csv.DictReader(f)}
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_COPILOT)
        w.writeheader()
        for p in perguntas:
            linha = existentes.get(p.id, {})
            w.writerow({**{c: linha.get(c, "") for c in COLUNAS_COPILOT}, "id": p.id, "pergunta": p.pergunta,
                        "resposta_esperada": p.resposta_esperada})


RE_FRASE = __import__("re").compile(r"(?<=[.;:])\s+|\n+")
RE_PRAZO_FRASE = __import__("re").compile(r"\b(?:[A-Z]{1,3}\s*[+\-]\s*)?\d+\s*(?:du|d|dias?|horas?|h|meses|anos?|%)\b"
                                          r"|\bM[SA]?\s*[+\-]\s*\d+", __import__("re").I)


RE_ITEM_FRASE = __import__("re").compile(r"^\s*\d+(?:\.\d+)+\.?\s*")


def teste_busca(chunks: list, top_k: int = 3, max_perguntas: int = 40) -> dict:
    """Teste automático, sem pessoa e sem IA: para cada frase do documento que tem prazo/número de regra
    ("até 5du", "MS+3du", "90 dias"), tira o prazo da frase e busca o resto (completa a amostra com outras
    frases do documento, sem o número do item). O trecho de onde a frase saiu
    precisa voltar entre os `top_k` primeiros; se não volta, o Copilot também tende a não achá-lo."""
    textos = [getattr(c, "texto", None) or c["texto"] for c in chunks]
    if len(textos) < 2:
        return {"perguntas": 0, "acertos": 0, "taxa": 1.0, "erros": []}
    indice = BM25(textos)
    com_prazo, outras = [], []
    for i, t in enumerate(textos):
        for frase in RE_FRASE.split(tx.markdown_para_texto(t)):
            frase = " ".join(frase.split())
            if not 8 <= len(frase.split()) <= 60:
                continue
            if RE_PRAZO_FRASE.search(frase):
                com_prazo.append((i, RE_PRAZO_FRASE.sub(" ", frase)))   # o prazo é a "resposta": sai da busca
            else:
                outras.append((i, RE_ITEM_FRASE.sub(" ", frase)))
    # prioridade para as frases com prazo; completa com frases comuns espalhadas pelo documento
    amostra = com_prazo[:: max(1, len(com_prazo) // max_perguntas)][:max_perguntas]
    falta = max_perguntas - len(amostra)
    if falta and outras:
        amostra += outras[:: max(1, len(outras) // falta)][:falta]
    erros = []
    for i, consulta in amostra:
        ordem = indice.ranking(consulta)
        # frase repetida em mais de um chunk (ex.: cabeçalho de tabela) conta se qualquer um deles vier
        alvos = {j for j, t in enumerate(textos) if _norm(consulta) in _norm(t)} | {i}
        if not any(j in alvos for j in ordem[:top_k]):
            erros.append({"frase": consulta[:160], "chunk": i + 1,
                          "posicao": next(k for k, j in enumerate(ordem, 1) if j in alvos)})
    n = len(amostra)
    return {"perguntas": n, "acertos": n - len(erros), "taxa": (n - len(erros)) / n if n else 1.0, "erros": erros[:15]}
