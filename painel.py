"""Painel de acompanhamento da base CCEE.

Rodar:  streamlit run painel.py
O painel só lê o banco e dispara o coletor em segundo plano; fechar o navegador
não interrompe uma coleta em andamento.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ccee import config, db

st.set_page_config(page_title="Base CCEE – Coletor", page_icon="⚡", layout="wide")

PASTA_LOGS = config.DADOS / "logs"
NOMES_ETAPA = {
    "descoberto": "Encontrado", "baixado": "Baixado", "convertido": "Convertido",
    "validado": "Validado", "revisado": "Revisado", "exportado": "Exportado",
}
NOMES_STATUS = {
    "rodando": "🔄 Rodando", "ok": "✅ OK", "com_erros": "⚠️ Com erros",
    "falhou": "❌ Falhou", "interrompida": "⏹️ Interrompida",
}


def ler(sql: str, params=()) -> pd.DataFrame:
    con = db.conectar()
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def texto_resumo(r) -> str:
    """O resumo vem vazio (NaN) enquanto a execução ainda está rodando."""
    if not isinstance(r, str) or not r:
        return ""
    try:
        return ", ".join(f"{k}: {v}" for k, v in json.loads(r).items())
    except (ValueError, AttributeError):
        return r


def tem(v) -> bool:
    return v is not None and not pd.isna(v) and v != ""


def em_andamento() -> bool:
    return config.TRAVA.exists() and not ler("SELECT id FROM execucoes WHERE status='rodando'").empty


def disparar(args: list[str]) -> Path:
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    log = PASTA_LOGS / f"coleta_{datetime.now():%Y%m%d_%H%M%S}.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log, "w", encoding="utf-8") as f:
        subprocess.Popen(
            [sys.executable, "-m", "ccee.pipeline", *args],
            cwd=config.RAIZ, stdout=f, stderr=subprocess.STDOUT, creationflags=flags,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
    return log


# ------------------------------------------------------------------ Barra lateral
with st.sidebar:
    st.header("Ações")
    rodando = em_andamento()
    if rodando:
        st.info("Há uma coleta em andamento. Os botões voltam quando ela terminar.")
    escopo_todos = st.checkbox("Incluir histórico e itens fora do escopo", value=False,
                               help="Por padrão só baixa os documentos vigentes selecionados para o corpus.")
    extra = ["--todos"] if escopo_todos else []
    c1, c2 = st.columns(2)
    if c1.button("🔎 Descobrir", disabled=rodando, width="stretch",
                 help="Lê as páginas da CCEE e atualiza o catálogo (não baixa nada)."):
        st.session_state["log"] = str(disparar(["descobrir"]))
        st.rerun()
    if c2.button("⬇️ Baixar", disabled=rodando, width="stretch",
                 help="Baixa os pendentes. Se cair no meio, rode de novo: continua de onde parou."):
        st.session_state["log"] = str(disparar(["baixar", *extra]))
        st.rerun()
    if st.button("▶️ Descobrir + baixar", disabled=rodando, width="stretch", type="primary"):
        st.session_state["log"] = str(disparar(["tudo", *extra]))
        st.rerun()
    if st.button("🔁 Tentar de novo os que falharam", disabled=rodando, width="stretch"):
        st.session_state["log"] = str(disparar(["baixar", "--reprocessar-erros", *extra]))
        st.rerun()

    st.divider()
    st.caption("Importar PDFs baixados à mão")
    st.markdown(f"O site da CCEE bloqueia coleta automática (HTTP 403). Baixe os arquivos pelo seu navegador, "
                f"sem renomear, e coloque em `{config.PASTA_ENTRADA}`.")
    if st.button("📥 Importar PDFs da pasta", disabled=rodando, width="stretch"):
        config.PASTA_ENTRADA.mkdir(parents=True, exist_ok=True)
        st.session_state["log"] = str(disparar(["importar"]))
        st.rerun()

    st.divider()
    st.caption("Onde os dados ficam")
    st.markdown(f"**Arquivos:** `{config.PASTA_PDF}`  \n**Banco:** `{config.BANCO}`  \n**Logs:** `{PASTA_LOGS}`")
    st.caption(f"Fontes: [Regras]({config.URL_REGRAS}) · [PdCs]({config.URL_PDCS})")


# ------------------------------------------------------------------ Conteúdo
st.title("Base CCEE · Coletor de PdCs e Regras")


def mostrar_log():
    log = st.session_state.get("log")
    if not log:  # painel reaberto: mostra o log mais recente
        logs = sorted(PASTA_LOGS.glob("coleta_*.log")) if PASTA_LOGS.exists() else []
        log = str(logs[-1]) if logs else None
    if log and Path(log).exists():
        with st.expander("Saída da coleta atual / última", expanded=em_andamento()):
            linhas = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
            st.code("\n".join(linhas[-25:]) or "(aguardando saída...)", language=None)


@st.fragment(run_every=5)
def visao_ao_vivo():
    ultima = ler("SELECT * FROM execucoes ORDER BY id DESC LIMIT 1")
    if not ultima.empty:
        u = ultima.iloc[0]
        st.caption(f"Última execução #{u.id} · {u.comando} · {NOMES_STATUS.get(u.status, u.status)} · início {u.inicio}"
                   + (f" · fim {u.fim}" if tem(u.fim) else "")
                   + (f" · {texto_resumo(u.resumo)}" if texto_resumo(u.resumo) else ""))
        erros_exec = ler("SELECT mensagem FROM eventos WHERE execucao_id=? AND nivel='erro' ORDER BY id LIMIT 5",
                         (int(u.id),))
        if not erros_exec.empty and u.status != "rodando":
            st.error("A última coleta teve erros:\n\n" + "\n\n".join(f"- {m}" for m in erros_exec.mensagem))
    mostrar_log()

    docs = ler("SELECT * FROM documentos")
    if docs.empty:
        st.info("Catálogo vazio. Clique em **Descobrir** na barra lateral para ler o site da CCEE.")
        return
    sel = docs[docs.selecionado == 1]
    baixados = int((sel.etapa != "descoberto").sum())
    erros = int(sel.erro.notna().sum())
    alertas = int(sel.alerta.notna().sum())
    m = st.columns(5)
    m[0].metric("No catálogo (site)", len(docs))
    m[1].metric("Selecionados p/ corpus", len(sel))
    m[2].metric("Baixados", f"{baixados}/{len(sel)}")
    m[3].metric("Com erro", erros)
    m[4].metric("Com alerta", alertas)
    if len(sel):
        st.progress(baixados / len(sel), text=f"Progresso do download: {baixados} de {len(sel)}")

    # Funil por etapa e fonte
    pivot = (
        sel.assign(etapa=sel.etapa.map(NOMES_ETAPA), fonte=sel.fonte.map({"regras": "Regras", "pdc": "PdCs"}))
        .pivot_table(index="fonte", columns="etapa", values="id", aggfunc="count", fill_value=0)
        .reindex(columns=[NOMES_ETAPA[e] for e in db.ETAPAS], fill_value=0)
    )
    st.caption("Documentos selecionados por etapa (as etapas depois de *Baixado* chegam na próxima fase)")
    st.dataframe(pivot, width="stretch")



visao_ao_vivo()

aba_docs, aba_alertas, aba_exec, aba_evt = st.tabs(["📄 Documentos", "⚠️ Alertas e erros", "🕓 Execuções", "📜 Eventos"])

with aba_docs:
    docs = ler("SELECT * FROM documentos ORDER BY fonte, codigo, publicado_em DESC")
    if not docs.empty:
        f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
        fonte = f1.multiselect("Fonte", ["regras", "pdc"], default=["regras", "pdc"])
        so_sel = f2.toggle("Só selecionados", value=True)
        etapas = f3.multiselect("Etapa", db.ETAPAS, default=[])
        busca = f4.text_input("Buscar no título", "")
        v = docs[docs.fonte.isin(fonte)]
        if so_sel:
            v = v[v.selecionado == 1]
        if etapas:
            v = v[v.etapa.isin(etapas)]
        if busca:
            v = v[v.titulo.str.contains(busca, case=False, na=False)]
        v = v.assign(etapa=v.etapa.map(NOMES_ETAPA), selecionado=v.selecionado.astype(bool))
        st.caption(f"{len(v)} documento(s)")
        st.dataframe(
            v[["fonte", "codigo", "titulo", "versao", "vigencia_inicio", "vigencia_fim", "etapa", "selecionado",
               "motivo_selecao", "alerta", "erro", "ato_aneel", "publicado_em", "tamanho_bytes", "caminho_local",
               "url", "visto_em"]],
            width="stretch", hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Link no site"), "fonte": "Fonte", "codigo": "Código",
                "titulo": "Título", "versao": "Versão", "etapa": "Etapa", "selecionado": "No corpus",
                "alerta": "Alerta", "erro": "Erro", "publicado_em": "Publicado em",
                "ato_aneel": "Ato ANEEL", "vigencia_inicio": "Vigência início", "vigencia_fim": "Vigência fim",
                "caminho_local": "Salvo em", "tamanho_bytes": st.column_config.NumberColumn("Bytes", format="%d"),
                "visto_em": "Visto no site em", "motivo_selecao": "Por que (não) entra",
            },
        )
        st.download_button("Exportar esta lista (CSV)", v.to_csv(index=False).encode("utf-8-sig"),
                           "catalogo_ccee.csv", "text/csv")

with aba_alertas:
    st.caption("Inconsistências do site (ex.: título com uma versão e link para outra) e falhas de download. "
               "Nada aqui é corrigido automaticamente: revise antes de seguir para a conversão.")
    a = ler("SELECT fonte, codigo, titulo, versao, alerta, erro, tentativas, url FROM documentos "
            "WHERE selecionado=1 AND (alerta IS NOT NULL OR erro IS NOT NULL) ORDER BY erro IS NULL, fonte, codigo")
    if a.empty:
        st.success("Nenhum alerta ou erro nos documentos selecionados.")
    else:
        st.dataframe(a, width="stretch", hide_index=True,
                     column_config={"url": st.column_config.LinkColumn("Link")})

with aba_exec:
    e = ler("SELECT * FROM execucoes ORDER BY id DESC LIMIT 200")
    if not e.empty:
        e["status"] = e.status.map(lambda s: NOMES_STATUS.get(s, s))
        e["resumo"] = e.resumo.map(texto_resumo)
        st.dataframe(e, width="stretch", hide_index=True)

with aba_evt:
    niveis = st.multiselect("Nível", ["info", "aviso", "erro", "mudanca"], default=["aviso", "erro", "mudanca"])
    ev = ler("SELECT e.em, e.execucao_id AS execucao, e.nivel, e.documento_id, d.titulo, e.mensagem "
             "FROM eventos e LEFT JOIN documentos d ON d.id = e.documento_id ORDER BY e.id DESC LIMIT 1000")
    if not ev.empty:
        st.dataframe(ev[ev.nivel.isin(niveis)], width="stretch", hide_index=True)
