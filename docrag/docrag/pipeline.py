"""Etapas do docrag: importar → converter → verificar (portão + chunks) → exportar → avaliar."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import traceback
from pathlib import Path

from . import arbitro, avaliacao, chunking, conversores, exportar, glossario, metadados, qualidade
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


def processo_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # no Windows os.kill(pid, 0) encerraria o processo; consulta pela API
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        codigo = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(codigo))
        k32.CloseHandle(h)
        return bool(ok) and codigo.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def execucao_em_andamento(perfil) -> bool:
    """Há trava E o processo que a criou ainda está vivo (trava de execução que caiu não conta)."""
    arq = perfil.pasta_trabalho / ".rodando"
    try:
        pid = int(arq.read_text(encoding="utf-8").strip() or 0)
    except (FileNotFoundError, ValueError):
        return False
    return processo_vivo(pid)


class Trava:
    """Impede duas execuções ao mesmo tempo (ex.: dois cliques no painel). Trava velha é limpa sozinha."""

    def __init__(self, perfil):
        self.perfil = perfil
        self.arquivo = perfil.pasta_trabalho / ".rodando"

    def __enter__(self):
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        if self.arquivo.exists() and not execucao_em_andamento(self.perfil):
            self.arquivo.unlink(missing_ok=True)
        try:
            fd = os.open(self.arquivo, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit(f"Já há uma execução em andamento ({self.arquivo}).")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.arquivo.unlink(missing_ok=True)


# --------------------------------------------------------------------------- importar
def abrir_zips(perfil, ex: Execucao | None = None) -> int:
    """ZIP baixado da CCEE na pasta de entrada: extrai os PDFs numa pasta ao lado, com o nome do ZIP.
    Só extrai uma vez (a pasta existir basta). Caminhos perigosos dentro do ZIP são ignorados."""
    import zipfile
    n = 0
    for z in sorted(perfil.pasta_entrada.rglob("*.zip"), key=lambda p: str(p).lower()):
        destino = z.with_suffix("")
        if destino.exists():
            continue
        try:
            with zipfile.ZipFile(z) as arq:
                for item in arq.infolist():
                    nome = Path(item.filename)
                    if item.is_dir() or nome.suffix.lower() != ".pdf" or nome.is_absolute() or ".." in nome.parts:
                        continue
                    alvo = destino / nome
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    with arq.open(item) as orig, open(alvo, "wb") as dest:
                        shutil.copyfileobj(orig, dest)
                    n += 1
            destino.mkdir(exist_ok=True)
            if ex:
                ex.evento("info", f"ZIP aberto: {z.name} → {destino.name}/")
        except zipfile.BadZipFile:
            if ex:
                ex.evento("erro", f"{z.name}: o ZIP está corrompido ou incompleto. Baixe de novo.")
    return n


def pdfs_na_entrada(perfil) -> list[Path]:
    return sorted((p for p in perfil.pasta_entrada.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
                  key=lambda p: str(p).lower())


def importar(perfil, con, ex: Execucao) -> list[int]:
    perfil.pasta_entrada.mkdir(parents=True, exist_ok=True)
    abrir_zips(perfil, ex)
    novos = []
    # Lê também as subpastas: pode jogar as pastas organizadas inteiras em "entrada".
    for pdf in pdfs_na_entrada(perfil):
        nome = pdf.relative_to(perfil.pasta_entrada).as_posix()   # ex. "PdCs/Modulo 1/1.6.pdf"
        h = sha256(pdf)
        if con.execute("SELECT 1 FROM documentos WHERE sha256=?", (h,)).fetchone():
            continue
        try:
            info = conversores.ler_pdf(pdf)
        except Exception as e:  # PDF corrompido ou protegido
            ex.evento("erro", f"{nome}: não consegui abrir o PDF ({e}).")
            continue
        inicio = "\n".join(info.textos[:3])
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


def reextrair_metadados(perfil, con, ex: Execucao, doc):
    """Metadados ainda não conferidos são tirados de novo (as regras do perfil podem ter melhorado)."""
    if doc["metadados_ok"] or doc["metadados_origem"] not in (None, "extraido"):
        return
    pdf = pasta_doc(perfil, doc) / "original.pdf"
    tipo = perfil.tipo(doc["tipo"])
    n = tipo.paginas_metadados if tipo else 3
    meta = metadados.extrair(perfil, "\n".join(conversores.ler_pdf(pdf).textos[:n]))
    novos = {c: meta.get(c) for c in CAMPOS_METADADOS if meta.get(c) and meta.get(c) != doc[c]}
    if novos:
        con.execute(f"UPDATE documentos SET {', '.join(c + '=?' for c in novos)}, metadados_origem='extraido' WHERE id=?",
                    (*novos.values(), doc["id"]))
        con.commit()
        ex.evento("info", f"{doc['arquivo']}: metadados atualizados {novos}.", doc["id"])


def confirmar_metadados(perfil, con, ex: Execucao, doc):
    """Conferência cruzada: se a segunda fonte e o nome do arquivo batem, os metadados ficam confirmados sozinhos."""
    if doc["metadados_ok"] or not perfil.automacao.confirmar_metadados:
        return
    tipo = perfil.tipo(doc["tipo"])
    n = tipo.paginas_metadados if tipo else 3
    texto = "\n".join(conversores.ler_pdf(pasta_doc(perfil, doc) / "original.pdf").textos[:n])
    ok, motivos = metadados.conferencia_cruzada(perfil, dict(doc), texto)
    if ok:
        con.execute("UPDATE documentos SET metadados_ok=1, metadados_conferencia='automatica', metadados_motivo=NULL "
                    "WHERE id=?", (doc["id"],))
        ex.evento("info", f"{doc['arquivo']}: metadados confirmados automaticamente (versão {doc['versao']}, "
                          f"vigência {doc['vigencia_inicio']}): cabeçalho, tabela de revisões e nome do arquivo batem.",
                  doc["id"])
    else:
        con.execute("UPDATE documentos SET metadados_motivo=? WHERE id=?", ("; ".join(motivos), doc["id"]))
        ex.evento("aviso", f"{doc['arquivo']}: metadados precisam de conferência: {'; '.join(motivos)}.", doc["id"])
    con.commit()


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


def texto_final(perfil, con, ex: Execucao, doc, info=None) -> tuple[str | None, list]:
    """final.md = melhor leitura de cada página (árbitro), a não ser que alguém tenha corrigido à mão."""
    pasta = pasta_doc(perfil, doc)
    principal_nome = doc["conversor_principal"] or perfil.principal_para(doc["tipo"])
    arq_final = pasta / "final.md"
    principal = _ler_conversao(pasta, principal_nome)
    if principal is None:
        return None, []
    if arq_final.exists():
        atual = hashlib.sha256(arq_final.read_bytes()).hexdigest()
        if doc["final_sha256"] and atual != doc["final_sha256"]:
            ex.evento("info", f"{doc['arquivo']}: usando final.md corrigido à mão.", doc["id"])
            return arq_final.read_text(encoding="utf-8"), []
    paginas, trocas = principal.paginas, []
    outras = [c for c in (_ler_conversao(pasta, n) for n in perfil.conversores if n != principal_nome) if c]
    if perfil.arbitro and outras:
        info = info or conversores.ler_pdf(pasta / "original.pdf")
        paginas, trocas = arbitro.arbitrar(info, principal, outras, perfil.limiares)
    arq_final.write_text(conversores.juntar_paginas(paginas), encoding="utf-8")
    con.execute("UPDATE documentos SET final_sha256=? WHERE id=?",
                (hashlib.sha256(arq_final.read_bytes()).hexdigest(), doc["id"]))
    return arq_final.read_text(encoding="utf-8"), trocas


def sorteado_auditoria(perfil, doc) -> bool:
    """Sorteio determinístico (pelo sha256 do PDF): o mesmo documento é sempre sorteado ou não."""
    n = perfil.automacao.auditoria_1_em
    return bool(n) and int(doc["sha256"][:12], 16) % n == 0


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
    final, trocas = texto_final(perfil, con, ex, doc, info)
    rel = qualidade.avaliar(info, principal, verificador, perfil.limiares, final_md=final)
    for t in trocas:
        rel.verificacoes.append(qualidade.Verificacao(
            "arbitro", qualidade.INFO, f"Usei a leitura do {t.conversor} nesta página: {t.motivo}", t.pagina,
            {"conversor": t.conversor}))
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
    # Teste automático de busca: frases com prazo/número precisam achar o próprio trecho
    busca = avaliacao.teste_busca(lista)
    if busca["perguntas"]:
        bom = busca["taxa"] >= perfil.automacao.busca_minima
        msg = (f"Teste automático de busca: {busca['acertos']} de {busca['perguntas']} frases do documento (prioridade para as com prazo) acharam o "
               f"trecho certo entre os 3 primeiros ({busca['taxa']:.0%}).")
        if not bom:
            msg += " Abaixo do mínimo: os chunks podem estar grandes, mal cortados ou repetitivos."
        v = qualidade.Verificacao("busca", qualidade.INFO if bom else qualidade.AVISO, msg, detalhe=busca)
        rel.verificacoes.append(v)
        con.execute("INSERT INTO verificacoes (documento_id, codigo, nivel, pagina, mensagem, detalhe) VALUES (?,?,?,?,?,?)",
                    (doc["id"], v.codigo, v.nivel, None, v.mensagem, json.dumps(v.detalhe, ensure_ascii=False)))
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
    if rel.status == "ok" and sorteado_auditoria(perfil, doc) and doc["auditoria"] != "feita":
        con.execute("UPDATE documentos SET auditoria='pendente' WHERE id=?", (doc["id"],))
        con.commit()
        ex.evento("aviso", f"{doc['arquivo']}: sorteado para auditoria (1 em cada {perfil.automacao.auditoria_1_em}). "
                           "Passou em tudo sozinho, mas uma pessoa confere por amostragem para medir se o "
                           "portão continua confiável.", doc["id"])
    if aceite and anterior != rel.status:
        ex.evento("aviso", f"{doc['arquivo']}: status mudou de {anterior} para {rel.status}; o aceite anterior foi "
                           "descartado e precisa ser refeito.", doc["id"])
    return rel.status


def corrigir_pagina(perfil, doc, pagina: int, texto: str):
    """Troca o texto de uma página no final.md (correção feita na tela). Fica preservada como correção à mão."""
    arq = pasta_doc(perfil, doc) / "final.md"
    paginas = conversores.separar_paginas(arq.read_text(encoding="utf-8"))
    paginas[pagina - 1] = texto.strip("\n")
    arq.write_text(conversores.juntar_paginas(paginas), encoding="utf-8")


def pendencias(perfil, con) -> list[dict]:
    """O que precisa de uma pessoa, documento por documento, já em palavras simples."""
    itens = []
    for d in con.execute("SELECT * FROM documentos ORDER BY tipo, documento, versao").fetchall():
        d = dict(d)
        if d.get("erro"):
            itens.append({"doc": d, "tipo": "erro", "texto": d["erro"]})
            continue
        if d["etapa"] in ("importado", "convertido"):
            continue  # ainda na esteira
        if not d["metadados_ok"]:
            itens.append({"doc": d, "tipo": "metadados",
                          "texto": d.get("metadados_motivo") or "os metadados ainda não foram conferidos"})
        if d["status_qualidade"] in ("aviso", "falha") and not d["aceito_em"]:
            vs = [dict(v) for v in con.execute(
                "SELECT * FROM verificacoes WHERE documento_id=? AND nivel IN ('aviso','falha') "
                "ORDER BY CASE nivel WHEN 'falha' THEN 0 ELSE 1 END, pagina", (d["id"],))]
            itens.append({"doc": d, "tipo": "qualidade", "verificacoes": vs,
                          "texto": f"{len(vs)} ponto(s) que nenhuma das leituras resolveu sozinha"})
        if d.get("auditoria") == "pendente":
            itens.append({"doc": d, "tipo": "auditoria",
                          "texto": f"sorteado para auditoria (1 em cada {perfil.automacao.auditoria_1_em})"})
    return itens


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
    if doc.get("auditoria") == "pendente":
        return False, "sorteado para auditoria: falta uma pessoa conferir e aceitar"
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
    shutil.copyfile(perfil.pasta_saida / "sharepoint" / "glossario.md", perfil.pasta_saida / "notebooklm" / "glossario.md")
    con.executemany("UPDATE documentos SET etapa='exportado', atualizado_em=? WHERE id=?",
                    [(agora(), d["id"]) for d in aptos])
    con.commit()
    res["glossario"] = len(entradas)
    ex.evento("info", f"Exportados {res['chunks']} chunks de {res['vigentes']} documento(s) vigente(s) para "
                      f"{res['pasta']}; glossário com {len(entradas)} entradas. Para o NotebookLM: "
                      f"{res['vigentes'] + 1} arquivo(s) em {perfil.pasta_saida / 'notebooklm'} (um por documento + glossário).")
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
        con.execute("UPDATE execucoes SET status='interrompida', fim=? WHERE status='rodando'", (agora(),))
        ex = Execucao(con, comando + (" --forcar" if forcar else "") + (f" --ids {ids}" if ids else ""))
        resumo: dict = {}
        try:
            if comando in ("importar", "processar", "esteira"):
                resumo["importados"] = len(importar(perfil, con, ex))
            if comando in ("processar", "verificar", "esteira"):
                sql = "SELECT * FROM documentos" + (f" WHERE id IN ({','.join('?' * len(ids))})" if ids else "")
                for doc in con.execute(sql + " ORDER BY id", ids or []).fetchall():
                    if comando == "esteira" and not ids and doc["etapa"] in ("verificado", "exportado"):
                        continue  # na esteira automática, o que já foi conferido não é refeito
                    if comando in ("processar", "esteira") and not converter(perfil, con, ex, doc, forcar):
                        continue
                    doc = con.execute("SELECT * FROM documentos WHERE id=?", (doc["id"],)).fetchone()
                    reextrair_metadados(perfil, con, ex, doc)
                    doc = con.execute("SELECT * FROM documentos WHERE id=?", (doc["id"],)).fetchone()
                    confirmar_metadados(perfil, con, ex, doc)
                    doc = con.execute("SELECT * FROM documentos WHERE id=?", (doc["id"],)).fetchone()
                    if doc["etapa"] == "importado":
                        continue
                    st = verificar(perfil, con, ex, doc)
                    if st:
                        resumo[st] = resumo.get(st, 0) + 1
            if comando in ("exportar", "esteira"):
                resumo.update(exportar_tudo(perfil, con, ex))
            if comando == "avaliar":
                resumo.update(avaliar(perfil, con, ex))
            ex.terminar(resumo)
            return 1 if ex.erros else 0
        except Exception as e:
            ex.evento("erro", f"Falha inesperada: {e}\n{traceback.format_exc()}")
            ex.terminar(resumo, falhou=True)
            return 2
