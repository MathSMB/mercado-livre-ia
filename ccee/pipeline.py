"""Linha de comando do coletor.

    python -m ccee.pipeline descobrir     # lê o site e atualiza o catálogo
    python -m ccee.pipeline baixar        # baixa o que está pendente (retoma de onde parou)
    python -m ccee.pipeline tudo          # descobrir + baixar
    python -m ccee.pipeline importar      # cataloga os PDFs baixados à mão em dados/entrada
    python -m ccee.pipeline status        # resumo no terminal

Opções: --todos (inclui histórico e itens fora do escopo no download),
        --limite N (baixa no máximo N arquivos), --reprocessar-erros
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import config, db
from .descoberta import descobrir_pdcs, descobrir_regras
from .download import baixar
from .http import Cliente
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


def etapa_descobrir(con, cliente, exec_id, log) -> Counter:
    c = Counter()
    for nome, func in (("Regras", descobrir_regras), ("PdCs", descobrir_pdcs)):
        try:
            docs = func(cliente)
        except Exception as e:  # noqa: BLE001
            db.evento(con, exec_id, "erro", f"Falha ao ler a página de {nome}: {e}")
            log(f"ERRO ao ler {nome}: {e}")
            c["falhas_fonte"] += 1
            continue
        with db.transacao(con):
            for d in docs:
                c[db.registrar_descoberta(con, exec_id, d)] += 1
                if d.get("alerta") and d.get("selecionado"):
                    db.evento(con, exec_id, "aviso", d["alerta"], d["id"])
        sel = sum(d["selecionado"] for d in docs)
        db.evento(con, exec_id, "info", f"{nome}: {len(docs)} itens no site, {sel} selecionados para o corpus")
        log(f"{nome}: {len(docs)} itens no site, {sel} selecionados")
        c[f"itens_{nome.lower()}"] = len(docs)
        c[f"selecionados_{nome.lower()}"] = sel
    return c


def etapa_baixar(con, cliente, exec_id, log, todos=False, limite=None, reprocessar_erros=False) -> Counter:
    c = Counter()
    filtro = "" if todos else "AND selecionado=1"
    filtro_erro = "" if reprocessar_erros else "AND erro IS NULL"
    pendentes = con.execute(
        f"SELECT * FROM documentos WHERE etapa='descoberto' {filtro} {filtro_erro} ORDER BY fonte, codigo"
    ).fetchall()
    if limite:
        pendentes = pendentes[:limite]
    log(f"{len(pendentes)} arquivo(s) para baixar")
    for i, linha in enumerate(pendentes, 1):
        doc = dict(linha)
        try:
            r = baixar(cliente, doc)
            con.execute(
                "UPDATE documentos SET etapa='baixado', erro=NULL, caminho_local=?, sha256=?, tamanho_bytes=?,"
                " nome_arquivo=COALESCE(nome_arquivo, ?), tentativas=tentativas+1, atualizado_em=? WHERE id=?",
                (r["caminho"], r["sha256"], r["tamanho_bytes"], r["nome_arquivo"], db.agora(), doc["id"]),
            )
            con.commit()
            c["baixados"] += 1
            log(f"[{i}/{len(pendentes)}] ok  {doc['titulo'][:70]}")
        except Exception as e:  # noqa: BLE001
            con.execute(
                "UPDATE documentos SET erro=?, tentativas=tentativas+1, atualizado_em=? WHERE id=?",
                (str(e)[:500], db.agora(), doc["id"]),
            )
            con.commit()
            db.evento(con, exec_id, "erro", f"Falha no download: {e}", doc["id"])
            c["erros"] += 1
            log(f"[{i}/{len(pendentes)}] ERRO {doc['titulo'][:60]}: {e}")
    return c


def executar(comando: str, todos=False, limite=None, reprocessar_erros=False, log=print, pasta=None) -> int:
    con = db.conectar()
    with Trava():
        _marcar_interrompidas(con)
        exec_id = db.iniciar_execucao(con, comando + (" --todos" if todos else ""))
        cliente = None if comando == "importar" else Cliente()
        resumo: Counter = Counter()
        status = "ok"
        try:
            if comando in ("descobrir", "tudo"):
                resumo += etapa_descobrir(con, cliente, exec_id, log)
            if comando in ("baixar", "tudo"):
                resumo += etapa_baixar(con, cliente, exec_id, log, todos, limite, reprocessar_erros)
            if comando == "importar":
                destino = pasta or config.PASTA_ENTRADA
                destino.mkdir(parents=True, exist_ok=True)
                resumo += importar_pasta(con, exec_id, destino, log)
            if resumo.get("erros") or resumo.get("falhas_fonte"):
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
    ap = argparse.ArgumentParser(prog="python -m ccee.pipeline", description="Coletor da base CCEE")
    ap.add_argument("comando", choices=["descobrir", "baixar", "tudo", "importar", "status"])
    ap.add_argument("--todos", action="store_true", help="baixa também histórico e itens fora do escopo")
    ap.add_argument("--limite", type=int, help="baixa no máximo N arquivos")
    ap.add_argument("--reprocessar-erros", action="store_true", help="tenta de novo os que falharam")
    ap.add_argument("--pasta", help="pasta com os PDFs para importar (padrão: dados/entrada)")
    a = ap.parse_args(argv)
    if a.comando == "status":
        print(status_texto())
        return 0
    return executar(a.comando, a.todos, a.limite, a.reprocessar_erros, log=lambda m: print(m, flush=True),
                    pasta=Path(a.pasta) if a.pasta else None)


if __name__ == "__main__":
    sys.exit(main())
