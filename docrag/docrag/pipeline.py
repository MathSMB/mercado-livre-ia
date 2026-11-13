"""Etapas do docrag: importar → converter → verificar (portão + chunks) → exportar → avaliar."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import traceback
from pathlib import Path

from . import avaliacao, chunking, conversores, exportar, glossario, metadados, qualidade
from . import texto as tx
from .banco import CAMPOS_METADADOS, Execucao, aberto, agora


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def pasta_doc(perfil, doc) -> Path:
    return perfil.pasta_trabalho / "docs" / f"{doc['id']:04d}-{tx.slug(Path(doc['arquivo']).stem, 40)}"


class Trava:
    """Impede duas execuções ao mesmo tempo (ex.: dois cliques no painel)."""

    def __init__(self, perfil):
        self.arquivo = perfil.pasta_trabalho / ".rodando"

    def __enter__(self):
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.arquivo, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit(f"Já há uma execução em andamento ({self.arquivo}). Se não houver, apague esse arquivo.")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.arquivo.unlink(missing_ok=True)


# --------------------------------------------------------------------------- importar
def importar(perfil, con, ex: Execucao) -> list[int]:
    perfil.pasta_entrada.mkdir(parents=True, exist_ok=True)
    novos = []
    # Lê também as subpastas: pode jogar as pastas organizadas inteiras em "entrada".
    pdfs = [p for p in perfil.pasta_entrada.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
    for pdf in sorted(pdfs, key=lambda p: str(p).lower()):
        nome = pdf.relative_to(perfil.pasta_entrada).as_posix()   # ex. "PdCs/Modulo 1/1.6.pdf"
        h = sha256(pdf)
        if con.execute("SELECT 1 FROM documentos WHERE sha256=?", (h,)).fetchone():
            continue
        try:
            info = conversores.ler_pdf(pdf)
        except Exception as e:  # PDF corrompido ou protegido
            ex.evento("erro", f"{nome}: não consegui abrir o PDF ({e}).")
            continue
        inicio = "\n".join(info.textos[:2])
        meta = metadados.extrair(perfil, inicio)
        origem = "extraido" if meta else None
        conferido = 0
        do_csv = metadados.do_csv(perfil, nome, h)
        if do_csv:
            meta.update({k: v for k, v in do_csv.items() if k in CAMPOS_METADADOS and v})
            origem = "documentos.csv"
            conferido = 1 if (do_csv.get("conferido") or "").lower() in ("sim", "s", "1", "x", "true") else 0
        doc_id = con.execute(
            f"INSERT INTO documentos (arquivo, sha256, paginas, {', '.join(CAMPOS_METADADOS)}, metadados_origem, "
            f"metadados_ok, importado_em, atualizado_em) VALUES (?,?,?,{','.join('?' * len(CAMPOS_METADADOS))},?,?,?,?)",
            (nome, h, info.n_paginas, *[meta.get(c) for c in CAMPOS_METADADOS], origem, conferido, agora(), agora()),
        ).lastrowid
        mesmo_nome = con.execute("SELECT id FROM documentos WHERE arquivo=? AND id<>?", (nome, doc_id)).fetchall()
        if mesmo_nome:
            ex.evento("aviso", f"{nome}: já existia um arquivo com esse nome e conteúdo diferente "
                               f"(ids {[r[0] for r in mesmo_nome]}). Confira se é uma versão nova.", doc_id)
        destino = pasta_doc(perfil, {"id": doc_id, "arquivo": nome})
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, destino / "original.pdf")
        con.commit()
        ex.evento("info", f"Importado: {nome} ({info.n_paginas} páginas, tipo {meta.get('tipo') or '?'}).", doc_id)
        novos.append(doc_id)
    return novos


# --------------------------------------------------------------------------- converter
def _ja_convertido(con, doc_id: int, conversor: str) -> bool:
    return con.execute("SELECT 1 FROM conversoes WHERE documento_id=? AND conversor=?", (doc_id, conversor)).fetchone() is not None


def converter(perfil, con, ex: Execucao, doc, forcar: bool = False) -> bool:
    pasta = pasta_doc(perfil, doc)
    pdf = pasta / "original.pdf"
    info = conversores.ler_pdf(pdf)
    principal = perfil.principal_para(doc["tipo"])
    ordem = [principal] + [c for c in perfil.conversores if c != principal]
    ok_principal = True
    for nome in ordem:
        if not forcar and _ja_convertido(con, doc["id"], nome) and (pasta / f"{nome}.md").exists():
            continue
        ex.evento("info", f"{doc['arquivo']}: convertendo com {nome}…", doc["id"])
        try:
            conv = conversores.converter(nome, pdf, info, perfil)
        except ImportError as e:
            ex.evento("erro", f"{nome} não está instalado ({e}). Rode: pip install -r requirements.txt", doc["id"])
            ok_principal &= nome != principal
            continue
        except Exception as e:
            ex.evento("erro", f"{doc['arquivo']}: {nome} falhou: {e}\n{traceback.format_exc(limit=3)}", doc["id"])
            ok_principal &= nome != principal
            continue
        (pasta / f"{nome}.md").write_text(conv.markdown, encoding="utf-8")
        con.execute("INSERT OR REPLACE INTO conversoes VALUES (?,?,?,?,?,?,?)",
                    (doc["id"], nome, conv.versao, conv.segundos, str(pasta / f"{nome}.md"),
                     json.dumps(conv.avisos, ensure_ascii=False), agora()))
        con.commit()
        ex.evento("info", f"{doc['arquivo']}: {nome} terminou em {conv.segundos:.0f}s.", doc["id"])
    if not ok_principal:
        con.execute("UPDATE documentos SET erro=?, atualizado_em=? WHERE id=?",
                    (f"Conversor principal ({principal}) falhou", agora(), doc["id"]))
        return False
    con.execute("UPDATE documentos SET etapa='convertido', conversor_principal=?, erro=NULL, atualizado_em=? WHERE id=?",
                (principal, agora(), doc["id"]))
    con.commit()
    return True


# --------------------------------------------------------------------------- verificar
def _ler_conversao(pasta: Path, nome: str) -> conversores.Conversao | None:
    arq = pasta / f"{nome}.md"
    if not arq.exists():
        return None
    return conversores.Conversao(nome, "", conversores.separar_paginas(arq.read_text(encoding="utf-8")))


def texto_final(perfil, con, ex: Execucao, doc) -> str | None:
    """final.md = cópia do principal, a não ser que alguém tenha corrigido à mão (aí é preservado)."""
    pasta = pasta_doc(perfil, doc)
    principal = doc["conversor_principal"] or perfil.principal_para(doc["tipo"])
    arq_principal, arq_final = pasta / f"{principal}.md", pasta / "final.md"
    if not arq_principal.exists():
        return None
    if arq_final.exists():
        atual = hashlib.sha256(arq_final.read_bytes()).hexdigest()
        if doc["final_sha256"] and atual != doc["final_sha256"]:
            ex.evento("info", f"{doc['arquivo']}: usando final.md corrigido à mão.", doc["id"])
            return arq_final.read_text(encoding="utf-8")
    shutil.copyfile(arq_principal, arq_final)
    con.execute("UPDATE documentos SET final_sha256=? WHERE id=?",
                (hashlib.sha256(arq_final.read_bytes()).hexdigest(), doc["id"]))
    return arq_final.read_text(encoding="utf-8")


def verificar(perfil, con, ex: Execucao, doc) -> str | None:
    pasta = pasta_doc(perfil, doc)
    info = conversores.ler_pdf(pasta / "original.pdf")
    principal_nome = doc["conversor_principal"] or perfil.principal_para(doc["tipo"])
    principal = _ler_conversao(pasta, principal_nome)
    if principal is None:
        ex.evento("erro", f"{doc['arquivo']}: sem conversão do {principal_nome}; rode Processar.", doc["id"])
        return None
    outros = [c for c in perfil.conversores if c != principal_nome]
    verificador = next((v for v in (_ler_conversao(pasta, c) for c in outros) if v), None)
    final = texto_final(perfil, con, ex, doc)
    rel = qualidade.avaliar(info, principal, verificador, perfil.limiares, final_md=final)
    if verificador is None and outros:
        rel.verificacoes.append(qualidade.Verificacao("segunda_leitura", qualidade.AVISO,
                                                      "Sem a conversão de verificação: o portão ficou só com o PDF."))
    con.execute("DELETE FROM verificacoes WHERE documento_id=?", (doc["id"],))
    con.executemany("INSERT INTO verificacoes (documento_id, codigo, nivel, pagina, mensagem, detalhe) VALUES (?,?,?,?,?,?)",
                    [(doc["id"], v.codigo, v.nivel, v.pagina, v.mensagem, json.dumps(v.detalhe, ensure_ascii=False))
                     for v in rel.verificacoes])
    # Chunks do texto final
    c = perfil.chunking
    lista = chunking.chunks(final, c.max_palavras, c.min_palavras, c.nivel_maximo)
    con.execute("DELETE FROM chunks WHERE documento_id=?", (doc["id"],))
    con.executemany("INSERT INTO chunks (documento_id, ordem, secao, numero, caminho, pagina_inicio, pagina_fim, palavras,"
                    " parte, de_partes, texto) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(doc["id"], ch.ordem, ch.secao, ch.numero, json.dumps(ch.caminho, ensure_ascii=False),
                      ch.pagina_inicio, ch.pagina_fim, ch.palavras, ch.parte, ch.de_partes, ch.texto) for ch in lista])
    # Aceite antigo deixa de valer se o resultado mudou
    anterior, aceite = con.execute("SELECT status_qualidade, aceito_em FROM documentos WHERE id=?", (doc["id"],)).fetchone()
    con.execute("UPDATE documentos SET etapa='verificado', status_qualidade=?, atualizado_em=?, "
                "aceito_em=CASE WHEN ?=status_qualidade THEN aceito_em END, "
                "aceito_nota=CASE WHEN ?=status_qualidade THEN aceito_nota END WHERE id=?",
                (rel.status, agora(), rel.status, rel.status, doc["id"]))
    con.commit()
    n = rel.contagem()
    nivel = "info" if rel.status == "ok" else "aviso"
    ex.evento(nivel, f"{doc['arquivo']}: portão {rel.status.upper()} ({n['falha']} falha(s), {n['aviso']} aviso(s)); "
                     f"{len(lista)} chunks.", doc["id"])
    if aceite and anterior != rel.status:
        ex.evento("aviso", f"{doc['arquivo']}: status mudou de {anterior} para {rel.status}; o aceite anterior foi "
                           "descartado e precisa ser refeito.", doc["id"])
    return rel.status


# --------------------------------------------------------------------------- exportar
def apto(perfil, doc) -> tuple[bool, str]:
    if doc["etapa"] not in ("verificado", "exportado"):
        return False, "ainda não verificado"
    falta = metadados.faltando(perfil, dict(doc))
    if falta:
        return False, f"metadados faltando: {', '.join(falta)}"
    if not doc["metadados_ok"]:
        return False, "metadados não conferidos"
    if doc["status_qualidade"] != "ok" and not doc["aceito_em"]:
        return False, f"portão com {doc['status_qualidade']} sem aceite"
    return True, ""


def _chunks_doc(con, doc_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM chunks WHERE documento_id=? ORDER BY ordem", (doc_id,))]


def exportar_tudo(perfil, con, ex: Execucao) -> dict:
    docs = [dict(d) for d in con.execute("SELECT * FROM documentos ORDER BY tipo, documento, versao")]
    aptos = []
    for d in docs:
        ok, motivo = apto(perfil, d)
        if ok:
            aptos.append(d)
        else:
            ex.evento("aviso", f"{d['arquivo']}: fora da exportação ({motivo}).", d["id"])
    if not aptos:
        ex.evento("aviso", "Nenhum documento apto para exportar.")
        return {"documentos": 0}
    chunks = {d["id"]: _chunks_doc(con, d["id"]) for d in aptos}
    relatorios = {}
    for d in aptos:
        vs = [dict(v) for v in con.execute("SELECT * FROM verificacoes WHERE documento_id=?", (d["id"],))]
        relatorios[d["id"]] = exportar.relatorio_markdown(perfil, d, vs)
    res = exportar.exportar(perfil, aptos, chunks, relatorios)
    vig = exportar.escolher_vigentes(aptos)
    fontes = [(exportar.rotulo_documento(perfil, d), (pasta_doc(perfil, d) / "final.md").read_text(encoding="utf-8"))
              for d in aptos if d["id"] in vig]
    entradas = glossario.montar(perfil, fontes)
    glossario.escrever(entradas, perfil.pasta_saida / "sharepoint" / "glossario.md",
                       perfil.pasta_saida / "glossario.csv", perfil.nome)
    con.executemany("UPDATE documentos SET etapa='exportado', atualizado_em=? WHERE id=?",
                    [(agora(), d["id"]) for d in aptos])
    con.commit()
    res["glossario"] = len(entradas)
    ex.evento("info", f"Exportados {res['chunks']} chunks de {res['vigentes']} documento(s) vigente(s) para "
                      f"{res['pasta']}; glossário com {len(entradas)} entradas.")
    return res


# --------------------------------------------------------------------------- avaliar
def avaliar(perfil, con, ex: Execucao, top_k: int = 5) -> dict:
    perguntas = avaliacao.ler_perguntas(perfil.arquivo("perguntas.csv"))
    if not perguntas:
        ex.evento("aviso", "perguntas.csv está vazio.")
        return {"perguntas": 0}
    docs = [dict(d) for d in con.execute("SELECT * FROM documentos WHERE etapa IN ('verificado','exportado')")]
    vig = exportar.escolher_vigentes(docs) or {d["id"] for d in docs}
    chunks = []
    for d in docs:
        if d["id"] not in vig:
            continue
        for ch in _chunks_doc(con, d["id"]):
            chunks.append({"id": f"{d['arquivo']}#{ch['ordem']} ({ch['secao']})",
                           "texto": exportar.texto_chunk(perfil, d, ch)})
    resultados = avaliacao.avaliar(perguntas, chunks, top_k)
    em = agora()
    con.executemany("INSERT INTO avaliacoes (em, pergunta_id, pergunta, resultado, posicao, chunk, detalhe) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [(em, r.pergunta.id, r.pergunta.pergunta, r.resultado, r.posicao, r.chunk, r.detalhe)
                     for r in resultados])
    con.commit()
    avaliacao.preparar_planilha_copilot(perguntas, perfil.arquivo("resultados_copilot.csv"))
    resumo = {k: sum(1 for r in resultados if r.resultado == k)
              for k in ("achou", "fora_do_top", "trecho_ausente", "sem_trechos")}
    ex.evento("info", f"Avaliação: {resumo} (top {top_k}, {len(chunks)} chunks).")
    return resumo


# --------------------------------------------------------------------------- orquestração
def rodar(perfil, comando: str, forcar: bool = False, ids: list[int] | None = None) -> int:
    with Trava(perfil), aberto(perfil.banco) as con:
        ex = Execucao(con, comando + (" --forcar" if forcar else "") + (f" --ids {ids}" if ids else ""))
        resumo: dict = {}
        try:
            if comando in ("importar", "processar"):
                resumo["importados"] = len(importar(perfil, con, ex))
            if comando in ("processar", "verificar"):
                sql = "SELECT * FROM documentos" + (f" WHERE id IN ({','.join('?' * len(ids))})" if ids else "")
                for doc in con.execute(sql + " ORDER BY id", ids or []).fetchall():
                    if comando == "processar" and not converter(perfil, con, ex, doc, forcar):
                        continue
                    doc = con.execute("SELECT * FROM documentos WHERE id=?", (doc["id"],)).fetchone()
                    if doc["etapa"] == "importado":
                        continue
                    st = verificar(perfil, con, ex, doc)
                    if st:
                        resumo[st] = resumo.get(st, 0) + 1
            if comando == "exportar":
                resumo.update(exportar_tudo(perfil, con, ex))
            if comando == "avaliar":
                resumo.update(avaliar(perfil, con, ex))
            ex.terminar(resumo)
            return 1 if ex.erros else 0
        except Exception as e:
            ex.evento("erro", f"Falha inesperada: {e}\n{traceback.format_exc()}")
            ex.terminar(resumo, falhou=True)
            return 2
