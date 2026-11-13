"""Painel do docrag.

Rodar:  streamlit run painel.py   (ou iniciar.bat no Windows)
O painel lê o banco e dispara o pipeline em segundo plano; fechar o navegador não
interrompe uma conversão em andamento.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from docrag import avaliacao, conversores, exportar, glossario, metadados, perfil as perfil_mod, pipeline
from docrag.banco import CAMPOS_METADADOS, agora, conectar
from docrag.cli import PERFIL_PADRAO

RAIZ = Path(__file__).resolve().parent
st.set_page_config(page_title="docrag – Conversão e chunking", page_icon="🧩", layout="wide")

PERFIL = perfil_mod.carregar(os.environ.get("DOCRAG_PERFIL", PERFIL_PADRAO))
PASTA_LOGS = PERFIL.pasta_trabalho / "logs"
for _p in (PERFIL.pasta_entrada, PERFIL.pasta_trabalho, PERFIL.pasta_saida):
    _p.mkdir(parents=True, exist_ok=True)  # a pasta de entrada já existe ao abrir o painel
NOMES_ETAPA = {"importado": "Importado", "convertido": "Convertido", "verificado": "Verificado", "exportado": "Exportado"}
ETAPAS = list(NOMES_ETAPA)
ICONE = {"ok": "✅ OK", "aviso": "⚠️ Aviso", "falha": "❌ Falha", None: "–"}
NOMES_STATUS = {"rodando": "🔄 Rodando", "ok": "✅ OK", "com_erros": "⚠️ Com erros", "falhou": "❌ Falhou",
                "interrompida": "⏹️ Interrompida"}


def ler(sql: str, params=()) -> pd.DataFrame:
    con = conectar(PERFIL.banco)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def gravar(sql: str, params=()):
    con = conectar(PERFIL.banco)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def tem(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and v != ""


def texto_resumo(r) -> str:
    if not isinstance(r, str) or not r:
        return ""
    try:
        return ", ".join(f"{k}: {v}" for k, v in json.loads(r).items())
    except (ValueError, AttributeError):
        return r


def em_andamento() -> bool:
    return pipeline.execucao_em_andamento(PERFIL)


def disparar(args: list[str]) -> Path:
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    log = PASTA_LOGS / f"execucao_{datetime.now():%Y%m%d_%H%M%S}.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log, "w", encoding="utf-8") as f:
        subprocess.Popen([sys.executable, "-X", "utf8", "-m", "docrag", "--perfil", str(PERFIL.pasta), *args], cwd=RAIZ,
                         stdout=f, stderr=subprocess.STDOUT, creationflags=flags,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return log


def botao(rotulo: str, args: list[str], ajuda: str, rodando: bool, primario: bool = False):
    if st.button(rotulo, disabled=rodando, width="stretch", help=ajuda, type="primary" if primario else "secondary"):
        st.session_state["log"] = str(disparar(args))
        st.rerun()


# ------------------------------------------------------------------ Barra lateral
with st.sidebar:
    st.header("Ações")
    rodando = em_andamento()
    if rodando:
        st.info("Há uma execução em andamento. Os botões voltam quando ela terminar.")
    st.markdown(f"Coloque os PDFs baixados em `{PERFIL.pasta_entrada}`.")
    botao("▶️ Processar PDFs novos", ["processar"], "Importa, converte com Docling e Marker, roda o portão de qualidade "
          "e gera os chunks. Os já convertidos não são refeitos.", rodando, primario=True)
    botao("🔁 Reverificar (após corrigir à mão)", ["verificar"], "Refaz portão e chunks sem reconverter. Use depois de "
          "editar algum final.md.", rodando)
    botao("📦 Exportar para o SharePoint", ["exportar"], "Monta a pasta de saída só com documentos aprovados "
          "(ou aceitos) e com metadados conferidos.", rodando)
    botao("❓ Rodar perguntas de teste", ["avaliar"], "Confere se a base contém e acha as respostas esperadas.", rodando)
    with st.expander("Reconverter tudo do zero"):
        st.caption("Só se mudou a versão do Docling/Marker ou as opções do perfil. Correções à mão em final.md "
                   "são mantidas.")
        botao("Reconverter", ["processar", "--forcar"], "Demorado.", rodando)

    st.divider()
    st.caption("Onde os dados ficam")
    st.markdown(f"**Perfil:** `{PERFIL.pasta}`  \n**Trabalho:** `{PERFIL.pasta_trabalho}`  \n"
                f"**Saída:** `{PERFIL.pasta_saida}`  \n**Banco:** `{PERFIL.banco}`")


# ------------------------------------------------------------------ Conteúdo
st.title("docrag · Conversão, portão de qualidade e chunks")
st.caption(PERFIL.nome + (f" – {PERFIL.descricao}" if PERFIL.descricao else ""))


def mostrar_log():
    log = st.session_state.get("log")
    if not log:
        logs = sorted(PASTA_LOGS.glob("execucao_*.log")) if PASTA_LOGS.exists() else []
        log = str(logs[-1]) if logs else None
    if log and Path(log).exists():
        with st.expander("Saída da execução atual / última", expanded=em_andamento()):
            linhas = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
            st.code("\n".join(linhas[-25:]) or "(aguardando saída...)", language=None)


@st.fragment(run_every=5)
def visao_ao_vivo():
    ultima = ler("SELECT * FROM execucoes ORDER BY id DESC LIMIT 1")
    if not ultima.empty:
        u = ultima.iloc[0]
        # Quando uma execução termina, recarrega a página inteira (tabelas fora deste bloco ficariam velhas).
        versao = f"{u.id}-{u.status}"
        anterior = st.session_state.get("versao_banco")
        st.session_state["versao_banco"] = versao
        if anterior is not None and anterior != versao:
            st.rerun(scope="app")
        st.caption(f"Última execução #{u.id} · {u.comando} · {NOMES_STATUS.get(u.status, u.status)} · início {u.inicio}"
                   + (f" · fim {u.fim}" if tem(u.fim) else "")
                   + (f" · {texto_resumo(u.resumo)}" if texto_resumo(u.resumo) else ""))
        erros = ler("SELECT mensagem FROM eventos WHERE execucao_id=? AND nivel='erro' ORDER BY id LIMIT 5", (int(u.id),))
        if not erros.empty and u.status != "rodando":
            st.error("A última execução teve erros:\n\n" + "\n\n".join(f"- {m.splitlines()[0]}" for m in erros.mensagem))
    mostrar_log()

    docs = ler("SELECT * FROM documentos")
    if docs.empty:
        st.info("Nenhum documento ainda. Coloque os PDFs na pasta de entrada e clique em **Processar PDFs novos**.")
        return
    aptos = sum(pipeline.apto(PERFIL, d)[0] for d in docs.to_dict("records"))
    m = st.columns(5)
    m[0].metric("Documentos", len(docs))
    m[1].metric("Portão OK", int((docs.status_qualidade == "ok").sum()))
    m[2].metric("Com aviso", int((docs.status_qualidade == "aviso").sum()))
    m[3].metric("Com falha", int((docs.status_qualidade == "falha").sum()))
    m[4].metric("Prontos p/ exportar", f"{aptos}/{len(docs)}")
    st.progress(aptos / len(docs), text=f"Prontos para o SharePoint: {aptos} de {len(docs)}")
    pivot = (docs.assign(etapa=docs.etapa.map(NOMES_ETAPA), tipo=docs.tipo.fillna("?"))
             .pivot_table(index="tipo", columns="etapa", values="id", aggfunc="count", fill_value=0)
             .reindex(columns=[NOMES_ETAPA[e] for e in ETAPAS], fill_value=0))
    st.caption("Documentos por etapa")
    st.dataframe(pivot, width="stretch")


visao_ao_vivo()

aba_docs, aba_qual, aba_chunks, aba_glos, aba_perg, aba_exec, aba_evt = st.tabs(
    ["📄 Documentos", "✅ Qualidade", "🧩 Chunks", "📚 Glossário", "❓ Perguntas de teste", "🕓 Execuções", "📜 Eventos"])


def escolher_doc(chave: str):
    docs = ler("SELECT * FROM documentos ORDER BY tipo, documento, versao")
    if docs.empty:
        st.info("Nenhum documento ainda.")
        return None
    rotulos = {int(d.id): f"#{d.id} · {exportar.rotulo_documento(PERFIL, d.to_dict())} · v{d.versao or '?'} · "
                          f"{ICONE.get(d.status_qualidade, '–')}" for _, d in docs.iterrows()}
    escolhido = st.selectbox("Documento", list(rotulos), format_func=rotulos.get, key=chave)
    return docs[docs.id == escolhido].iloc[0].to_dict()


# ------------------------------------------------------------------ Documentos e metadados
with aba_docs:
    st.caption("Confira os metadados de cada documento (tirados do texto da capa). Nada vai para o SharePoint sem "
               "**Conferido** marcado. Ao salvar, os dados vão também para `documentos.csv` do perfil, que fica no Git.")
    docs = ler("SELECT * FROM documentos ORDER BY tipo, documento, versao")
    if not docs.empty:
        docs[CAMPOS_METADADOS] = docs[CAMPOS_METADADOS].fillna("")
        docs["conferido"] = docs.metadados_ok.astype(bool)
        docs["pronto"] = [("✅" if ok else "⛔ " + motivo) for ok, motivo in
                          (pipeline.apto(PERFIL, d) for d in docs.to_dict("records"))]
        docs["portao"] = docs.status_qualidade.map(lambda s: ICONE.get(s, "–"))
        colunas = ["id", "arquivo", *CAMPOS_METADADOS, "conferido", "portao", "etapa", "pronto", "paginas", "erro"]
        editado = st.data_editor(
            # a chave muda quando o banco muda: a tabela nunca mostra dados velhos depois de uma execução
            docs[colunas], width="stretch", hide_index=True, key=f"editor_docs_{st.session_state.get('versao_banco')}",
            disabled=["id", "arquivo", "portao", "etapa", "pronto", "paginas", "erro"],
            column_config={
                "tipo": st.column_config.SelectboxColumn("Tipo", options=[t.nome for t in PERFIL.tipos]),
                "documento": "Documento", "titulo": "Título", "versao": "Versão",
                "vigencia_inicio": st.column_config.TextColumn("Vigência início", help="dd/mm/aaaa"),
                "vigencia_fim": st.column_config.TextColumn("Vigência fim", help="dd/mm/aaaa; vazio = em vigor"),
                "ato": "Ato de aprovação", "conferido": st.column_config.CheckboxColumn("Conferido"),
                "portao": "Portão", "etapa": "Etapa", "pronto": "Pronto p/ exportar", "paginas": "Páginas",
            })
        if st.button("💾 Salvar metadados", type="primary"):
            problemas = []
            for _, linha in editado.iterrows():
                d = {c: (linha[c] if tem(linha[c]) else None) for c in CAMPOS_METADADOS}
                falta = metadados.faltando(PERFIL, d)
                conferido = bool(linha.conferido) and not falta
                if linha.conferido and falta:
                    problemas.append(f"#{linha.id}: faltam {', '.join(falta)}; não marquei como conferido.")
                for c in ("vigencia_inicio", "vigencia_fim"):
                    if d[c] and not exportar.data(d[c]):
                        problemas.append(f"#{linha.id}: data inválida em {c} ({d[c]}). Use dd/mm/aaaa.")
                        conferido = False
                gravar(f"UPDATE documentos SET {', '.join(c + '=?' for c in CAMPOS_METADADOS)}, metadados_ok=?, "
                       "metadados_origem='painel', atualizado_em=? WHERE id=?",
                       (*[d[c] for c in CAMPOS_METADADOS], int(conferido), agora(), int(linha.id)))
                original = docs[docs.id == linha.id].iloc[0]
                metadados.salvar_no_csv(PERFIL, {**d, "arquivo": original.arquivo, "sha256": original.sha256,
                                                 "conferido": "sim" if conferido else ""})
            for p in problemas:
                st.warning(p)
            st.success("Metadados salvos.")


# ------------------------------------------------------------------ Qualidade
def pagina_lado_a_lado(doc: dict, pagina: int):
    pasta = pipeline.pasta_doc(PERFIL, doc)
    cols = st.columns(3)
    pdf = pasta / "original.pdf"
    if pdf.exists():
        info = conversores.ler_pdf(pdf)
        cols[0].markdown("**Texto do PDF**")
        cols[0].code(info.textos[pagina - 1] if pagina <= info.n_paginas else "", language=None, wrap_lines=True)
    for col, nome in zip(cols[1:], ["final", next((c for c in PERFIL.conversores if c != doc.get("conversor_principal")),
                                                   "marker")]):
        arq = pasta / f"{nome}.md"
        col.markdown(f"**{'Texto final (vai para a base)' if nome == 'final' else nome}**")
        if arq.exists():
            pags = conversores.separar_paginas(arq.read_text(encoding="utf-8"))
            col.code(pags[pagina - 1] if pagina <= len(pags) else "(página ausente)", language="markdown", wrap_lines=True)
        else:
            col.caption("(sem conversão)")


with aba_qual:
    doc = escolher_doc("doc_qual")
    if doc:
        v = ler("SELECT codigo, nivel, pagina, mensagem, detalhe FROM verificacoes WHERE documento_id=? "
                "ORDER BY CASE nivel WHEN 'falha' THEN 0 WHEN 'aviso' THEN 1 ELSE 2 END, pagina", (int(doc["id"]),))
        st.markdown(f"Status do portão: **{ICONE.get(doc['status_qualidade'], '–')}**"
                    + (f" · aceito em {doc['aceito_em']}: _{doc['aceito_nota']}_" if tem(doc.get("aceito_em")) else ""))
        so_problemas = st.toggle("Só avisos e falhas", value=True)
        vv = v[v.nivel != "ok"] if so_problemas else v
        st.dataframe(vv.assign(nivel=vv.nivel.map(ICONE))[["nivel", "pagina", "codigo", "mensagem"]], width="stretch",
                     hide_index=True, column_config={"nivel": "Nível", "pagina": "Página", "codigo": "Verificação",
                                                     "mensagem": "O que foi encontrado"})
        paginas = sorted({int(p) for p in vv.pagina.dropna()}) or list(range(1, int(doc["paginas"] or 1) + 1))
        pg = st.selectbox("Comparar a página", paginas, key="pg_qual")
        pagina_lado_a_lado(doc, pg)
        detalhes = vv[vv.pagina == pg]
        for _, linha in detalhes.iterrows():
            with st.expander(f"Detalhe: {linha.codigo} (p. {pg})"):
                st.json(json.loads(linha.detalhe or "{}"))
        st.divider()
        final = pipeline.pasta_doc(PERFIL, doc) / "final.md"
        st.markdown(f"**Para corrigir:** edite `{final}` (mantenha as marcas `<!-- página N -->`) e clique em "
                    "**Reverificar**. A correção é preservada mesmo se reconverter.")
        if doc["status_qualidade"] in ("aviso", "falha") and not tem(doc.get("aceito_em")):
            with st.form("aceite"):
                nota = st.text_area("Aceitar mesmo assim: o que você conferiu?",
                                    placeholder="Ex.: conferi as págs. 4 e 7 com o PDF; o fluxograma da p. 7 foi "
                                                "descrito à mão; diferença de tabela é só formatação.")
                if st.form_submit_button("Registrar aceite"):
                    if len(nota.strip()) < 10:
                        st.error("Escreva o que foi conferido (fica registrado no relatório de qualidade).")
                    else:
                        gravar("UPDATE documentos SET aceito_em=?, aceito_nota=? WHERE id=?",
                               (agora(), nota.strip(), int(doc["id"])))
                        st.rerun()


# ------------------------------------------------------------------ Chunks
with aba_chunks:
    doc = escolher_doc("doc_chunks")
    if doc:
        ch = ler("SELECT * FROM chunks WHERE documento_id=? ORDER BY ordem", (int(doc["id"]),))
        if ch.empty:
            st.info("Sem chunks: rode Processar.")
        else:
            c = st.columns(3)
            c[0].metric("Chunks", len(ch))
            c[1].metric("Palavras por chunk (mediana)", int(ch.palavras.median()))
            c[2].metric("Maior chunk (palavras)", int(ch.palavras.max()))
            st.dataframe(ch[["ordem", "secao", "pagina_inicio", "pagina_fim", "palavras", "parte", "de_partes"]],
                         width="stretch", hide_index=True,
                         column_config={"ordem": "Nº", "secao": "Seção", "pagina_inicio": "Pág. início",
                                        "pagina_fim": "Pág. fim", "palavras": "Palavras", "parte": "Parte",
                                        "de_partes": "De"})
            n = st.number_input("Ver o chunk nº", 1, len(ch), 1)
            linha = ch[ch.ordem == n].iloc[0].to_dict()
            st.caption("Exatamente como vai para o SharePoint:")
            st.code(exportar.texto_chunk(PERFIL, doc, linha), language="markdown", wrap_lines=True)


# ------------------------------------------------------------------ Glossário
with aba_glos:
    st.caption("Siglas e variáveis tiradas literalmente dos documentos verificados, mais as do `glossario.csv` do "
               "perfil (que vencem as automáticas). Na exportação vira `glossario.md` dentro da pasta do SharePoint.")
    docs = ler("SELECT * FROM documentos WHERE etapa IN ('verificado','exportado')")
    fontes = []
    for d in docs.to_dict("records"):
        f = pipeline.pasta_doc(PERFIL, d) / "final.md"
        if f.exists():
            fontes.append((exportar.rotulo_documento(PERFIL, d), f.read_text(encoding="utf-8")))
    ent = glossario.montar(PERFIL, fontes)
    if ent:
        tab = pd.DataFrame([e.__dict__ for e in ent])
        st.dataframe(tab, width="stretch", hide_index=True)
        st.download_button("Baixar como CSV", tab.to_csv(index=False).encode("utf-8-sig"), "glossario.csv", "text/csv")
    st.markdown(f"Para corrigir ou acrescentar um termo, edite `{PERFIL.arquivo('glossario.csv')}`.")


# ------------------------------------------------------------------ Perguntas de teste
with aba_perg:
    st.caption("Perguntas com a resposta certa, para medir a base. Em **trechos** cole frases exatas do documento "
               "que precisam estar no chunk que responde (separe várias com `||`). O teste local mede se a base "
               "contém e se uma busca acha o trecho; o teste do Copilot é registrado à mão abaixo.")
    arq = PERFIL.arquivo("perguntas.csv")
    perg = pd.read_csv(arq, dtype=str, keep_default_na=False) if arq.exists() else \
        pd.DataFrame(columns=avaliacao.COLUNAS_PERGUNTAS)
    perg = perg.reindex(columns=avaliacao.COLUNAS_PERGUNTAS, fill_value="")
    novo = st.data_editor(perg, num_rows="dynamic", width="stretch", hide_index=True, key="editor_perg")
    if st.button("💾 Salvar perguntas"):
        novo.to_csv(arq, index=False, encoding="utf-8-sig")
        st.success(f"Salvo em {arq}")

    res = ler("SELECT * FROM avaliacoes WHERE em=(SELECT MAX(em) FROM avaliacoes)")
    if not res.empty:
        st.subheader(f"Último teste local ({res.em.iloc[0]})")
        achou = int((res.resultado == "achou").sum())
        st.metric("Achou o trecho certo entre os 5 primeiros", f"{achou}/{len(res)}")
        nomes = {"achou": "✅ Achou", "fora_do_top": "⚠️ Existe, mas a busca não pôs no topo",
                 "trecho_ausente": "❌ Base não contém o trecho", "sem_trechos": "– Sem trechos"}
        st.dataframe(res.assign(resultado=res.resultado.map(nomes))[["pergunta_id", "pergunta", "resultado", "posicao",
                                                                     "chunk", "detalhe"]],
                     width="stretch", hide_index=True)

    st.subheader("Teste no Copilot (manual)")
    arq_c = PERFIL.arquivo("resultados_copilot.csv")
    if arq_c.exists():
        rc = pd.read_csv(arq_c, dtype=str, keep_default_na=False).reindex(columns=avaliacao.COLUNAS_COPILOT, fill_value="")
        rc_novo = st.data_editor(rc, width="stretch", hide_index=True, key="editor_copilot",
                                 disabled=["id", "pergunta", "resposta_esperada"],
                                 column_config={"correta": st.column_config.SelectboxColumn(options=["", "sim", "não", "parcial"]),
                                                "citou_fonte_certa": st.column_config.SelectboxColumn(options=["", "sim", "não"])})
        feitas = rc_novo[rc_novo.correta != ""]
        if len(feitas):
            st.metric("Acerto do Copilot", f"{(feitas.correta == 'sim').mean():.0%}", f"{len(feitas)} pergunta(s) testada(s)")
        if st.button("💾 Salvar resultados do Copilot"):
            rc_novo.to_csv(arq_c, index=False, encoding="utf-8-sig")
            st.success("Salvo.")
    else:
        st.caption("Rode **Rodar perguntas de teste** para criar a planilha.")


# ------------------------------------------------------------------ Execuções e eventos
with aba_exec:
    e = ler("SELECT * FROM execucoes ORDER BY id DESC LIMIT 200")
    if not e.empty:
        e["status"] = e.status.map(lambda s: NOMES_STATUS.get(s, s))
        e["resumo"] = e.resumo.map(texto_resumo)
        st.dataframe(e, width="stretch", hide_index=True)

with aba_evt:
    niveis = st.multiselect("Nível", ["info", "aviso", "erro"], default=["aviso", "erro"])
    ev = ler("SELECT e.em, e.execucao_id AS execucao, e.nivel, e.documento_id, d.arquivo, e.mensagem "
             "FROM eventos e LEFT JOIN documentos d ON d.id = e.documento_id ORDER BY e.id DESC LIMIT 1000")
    if not ev.empty:
        st.dataframe(ev[ev.nivel.isin(niveis)], width="stretch", hide_index=True)
