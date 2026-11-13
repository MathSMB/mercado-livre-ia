"""Metadados de cada documento: documento, título, versão, vigência e ato de aprovação.

Ordem de prioridade (a de baixo vence):
  1. extraído do texto das primeiras páginas, pelas regras do perfil;
  2. `documentos.csv` do perfil (conferido à mão, por nome do arquivo ou sha256);
  3. edição no painel.
Nada é exportado sem os campos obrigatórios e sem uma pessoa marcar "conferido".
"""
from __future__ import annotations

import csv
from pathlib import Path

from .banco import CAMPOS_METADADOS

COLUNAS_CSV = ["arquivo", "sha256", *CAMPOS_METADADOS, "conferido"]


def extrair(perfil, texto_inicio: str) -> dict[str, str]:
    tipo = perfil.tipo_de(texto_inicio)
    if not tipo:
        return {}
    return {"tipo": tipo.nome, **tipo.extrair(texto_inicio)}


def ler_csv(caminho: Path) -> list[dict[str, str]]:
    if not caminho.exists():
        return []
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in linha.items() if k} for linha in csv.DictReader(f)]


def do_csv(perfil, arquivo: str, sha256: str) -> dict[str, str] | None:
    for linha in ler_csv(perfil.arquivo("documentos.csv")):
        if (linha.get("sha256") and linha["sha256"].lower() == sha256) or linha.get("arquivo") == arquivo:
            return linha
    return None


def salvar_no_csv(perfil, valores: dict[str, str]):
    """Grava/atualiza a linha do documento no documentos.csv (fica versionado no Git)."""
    caminho = perfil.arquivo("documentos.csv")
    linhas = ler_csv(caminho)
    chave = valores["sha256"]
    linhas = [l for l in linhas if l.get("sha256") != chave and l.get("arquivo") != valores["arquivo"]]
    linhas.append({c: valores.get(c, "") or "" for c in COLUNAS_CSV})
    linhas.sort(key=lambda l: (l.get("tipo", ""), l.get("documento", ""), l.get("versao", "")))
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_CSV)
        w.writeheader()
        w.writerows(linhas)


def faltando(perfil, doc: dict) -> list[str]:
    return [c for c in perfil.campos_obrigatorios if not (doc.get(c) or "").strip()]


def data_valida(valor: str) -> bool:
    from datetime import datetime
    try:
        datetime.strptime(valor, "%d/%m/%Y")
        return True
    except (TypeError, ValueError):
        return False


def conferencia_cruzada(perfil, doc: dict, texto_inicio: str) -> tuple[bool, list[str]]:
    """Confirma os metadados sem pessoa: cada campo com regra em [tipos.conferir] precisa dar o mesmo valor
    por uma segunda fonte do texto, o nome do arquivo não pode contradizer, as datas precisam ser válidas e
    os obrigatórios precisam estar preenchidos. Devolve (confirmado, motivos legíveis)."""
    from pathlib import PurePosixPath
    import re

    tipo = perfil.tipo(doc.get("tipo"))
    if not tipo:
        return False, ["tipo de documento não reconhecido"]
    motivos = []
    falta = faltando(perfil, doc)
    if falta:
        motivos.append(f"faltam {', '.join(falta)}")
    for campo in ("vigencia_inicio", "vigencia_fim"):
        if (doc.get(campo) or "").strip() and not data_valida(doc[campo]):
            motivos.append(f"{campo} '{doc[campo]}' não é uma data válida")
    if not tipo.conferir:
        motivos.append("o perfil não tem regra de conferência cruzada para este tipo")
    segunda = tipo.extrair(texto_inicio, tipo.conferir)
    for campo in tipo.conferir:
        if campo not in segunda:
            motivos.append(f"não achei a segunda fonte de {campo}")
        elif (doc.get(campo) or "") != segunda[campo]:
            motivos.append(f"{campo}: '{doc.get(campo)}' no cabeçalho, mas '{segunda[campo]}' na segunda fonte")
    nome = PurePosixPath(doc.get("arquivo") or "").name
    for campo, padrao in tipo.arquivo.items():
        m = re.search(padrao, nome)
        if m and m.group(1) != (doc.get(campo) or ""):
            motivos.append(f"{campo}: '{doc.get(campo)}' no texto, mas '{m.group(1)}' no nome do arquivo")
    return not motivos, motivos
