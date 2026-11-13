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
