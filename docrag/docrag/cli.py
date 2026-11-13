"""Linha de comando.

    python -m docrag processar [--perfil perfis/ccee] [--forcar] [--ids 1 2]
    python -m docrag verificar          # refaz portão e chunks sem reconverter (após corrigir final.md)
    python -m docrag exportar           # monta a pasta do SharePoint
    python -m docrag avaliar            # roda as perguntas de teste
    python -m docrag aceitar 3 --nota "fluxograma da p. 7 descrito à mão"
    python -m docrag conferir 3         # marca os metadados do documento 3 como conferidos
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import metadados, perfil as perfil_mod, pipeline
from .banco import aberto, agora

PERFIL_PADRAO = os.environ.get("DOCRAG_PERFIL", str(Path(__file__).resolve().parent.parent / "perfis" / "ccee"))


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="docrag", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--perfil", default=PERFIL_PADRAO, help="pasta do perfil (padrão: perfis/ccee)")
    sub = ap.add_subparsers(dest="comando", required=True)
    for nome in ("importar", "processar", "verificar", "exportar", "avaliar"):
        p = sub.add_parser(nome)
        if nome in ("processar", "verificar"):
            p.add_argument("--ids", type=int, nargs="*", help="só estes documentos")
        if nome == "processar":
            p.add_argument("--forcar", action="store_true", help="reconverte mesmo o que já foi convertido")
    p = sub.add_parser("aceitar", help="aceita os avisos/falhas do portão de um documento")
    p.add_argument("id", type=int)
    p.add_argument("--nota", required=True, help="o que foi conferido e por que pode seguir")
    p = sub.add_parser("conferir", help="marca os metadados do documento como conferidos")
    p.add_argument("id", type=int)
    a = ap.parse_args(argv)
    perfil = perfil_mod.carregar(a.perfil)

    if a.comando in ("aceitar", "conferir"):
        with aberto(perfil.banco) as con:
            doc = con.execute("SELECT * FROM documentos WHERE id=?", (a.id,)).fetchone()
            if not doc:
                print(f"Documento {a.id} não existe.")
                return 1
            if a.comando == "aceitar":
                con.execute("UPDATE documentos SET aceito_em=?, aceito_nota=?, auditoria=CASE auditoria WHEN 'pendente' "
                            "THEN 'feita' ELSE auditoria END WHERE id=?", (agora(), a.nota, a.id))
            else:
                falta = metadados.faltando(perfil, dict(doc))
                if falta:
                    print(f"Faltam metadados: {', '.join(falta)}. Preencha no painel ou no documentos.csv.")
                    return 1
                con.execute("UPDATE documentos SET metadados_ok=1, metadados_conferencia='pessoa' WHERE id=?", (a.id,))
                metadados.salvar_no_csv(perfil, {**dict(doc), "conferido": "sim"})
        print("ok")
        return 0
    return pipeline.rodar(perfil, a.comando, forcar=getattr(a, "forcar", False), ids=getattr(a, "ids", None))
