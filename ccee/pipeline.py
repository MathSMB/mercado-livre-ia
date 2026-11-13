"""Linha de comando da base CCEE.

    python -m ccee.pipeline importar      # cataloga os PDFs/ZIPs baixados à mão em dados/entrada
    python -m ccee.pipeline importar --pasta C:\\Downloads\\ccee
    python -m ccee.pipeline status        # resumo no terminal

O site da CCEE bloqueia acesso automatizado (HTTP 403), então os arquivos são baixados
pelo navegador e este comando faz o resto. Ver D10 no registro de decisões.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import config, db
from .importar import importar_pasta


class Trava:
    """Impede duas execuções simultâneas (ex.: agendador + botão do painel)."""

    def __init__(self, caminho=None):
        self.caminho = caminho or config.TRAVA

    def __enter__(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        if self.caminho.exists():
            try:
                pid = int(self.caminho.read_text().strip() or 0)
            except ValueError:
                pid = 0
            if pid and _processo_vivo(pid):
                raise SystemExit(f"Já existe uma execução em andamento (processo {pid}).")
        self.caminho.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        self.caminho.unlink(missing_ok=True)


def _processo_vivo(pid: int) -> bool:
    if pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            codigo = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(codigo))
            ctypes.windll.kernel32.CloseHandle(h)
            return codigo.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _marcar_interrompidas(con):
    """Execuções que ficaram 'rodando' sem processo vivo (queda de energia, Ctrl+C)."""
    con.execute(
        "UPDATE execucoes SET status='interrompida', fim=COALESCE(fim, ?) WHERE status='rodando'",
        (db.agora(),),
    )
    con.commit()


def executar(comando: str = "importar", pasta: Path | None = None, log=print) -> int:
    con = db.conectar()
    with Trava():
        _marcar_interrompidas(con)
        exec_id = db.iniciar_execucao(con, comando)
        resumo: Counter = Counter()
        status = "ok"
        try:
            destino = pasta or config.PASTA_ENTRADA
            destino.mkdir(parents=True, exist_ok=True)
            resumo += importar_pasta(con, exec_id, destino, log)
            if resumo.get("erros"):
                status = "com_erros"
        except KeyboardInterrupt:
            status = "interrompida"
            raise
        except Exception as e:  # noqa: BLE001
            status = "falhou"
            db.evento(con, exec_id, "erro", f"Execução falhou: {e}")
            raise
        finally:
            db.finalizar_execucao(con, exec_id, status, dict(resumo))
            log(f"Execução {exec_id} terminou: {status} {dict(resumo)}")
    return 0 if status == "ok" else 1


def status_texto() -> str:
    con = db.conectar()
    linhas = con.execute(
        "SELECT fonte, etapa, COUNT(*) n, SUM(erro IS NOT NULL) e FROM documentos WHERE selecionado=1"
        " GROUP BY fonte, etapa ORDER BY fonte, etapa"
    ).fetchall()
    return "\n".join(f"{l['fonte']:7} {l['etapa']:11} {l['n']:4}  (com erro: {l['e']})" for l in linhas) or "Catálogo vazio."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ccee.pipeline", description="Base CCEE")
    ap.add_argument("comando", choices=["importar", "status"])
    ap.add_argument("--pasta", help="pasta com os PDFs para importar (padrão: dados/entrada)")
    a = ap.parse_args(argv)
    if a.comando == "status":
        print(status_texto())
        return 0
    return executar(a.comando, Path(a.pasta) if a.pasta else None, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
