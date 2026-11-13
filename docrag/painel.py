"""Painel do docrag (versão 2): uma esteira que trabalha sozinha e só chama você nas exceções.

Rodar:  streamlit run painel.py   (ou iniciar.bat no Windows)

Telas:
  Esteira          – o resumo do dia: o que está andando, o que precisa de você, o que foi publicado.
  Precisa de você  – só as exceções, com a imagem da página do PDF ao lado do texto, para corrigir ali.
  Pacote pronto    – as pastas para o NotebookLM e para o SharePoint, com o passo a passo.
  Detalhes         – tudo o que o programa conferiu e decidiu, com filtros (e as ferramentas avançadas).

O trabalho pesado roda em segundo plano (python -m docrag esteira); fechar o navegador não interrompe.
"""
from __future__ import annotations

import io
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
st.set_page_config(page_title="docrag", page_icon="🧩", layout="wide", initial_sidebar_state="collapsed")

PERFIL = perfil_mod.carregar(os.environ.get("DOCRAG_PERFIL", PERFIL_PADRAO))
PASTA_LOGS = PERFIL.pasta_trabalho / "logs"
for _p in (PERFIL.pasta_entrada, PERFIL.pasta_trabalho, PERFIL.pasta_saida):
    _p.mkdir(parents=True, exist_ok=True)

NOMES_VERIF = {
    "arbitro": "Escolha da leitura", "cobertura": "Palavras do PDF", "numeros": "Números e prazos",
    "concordancia": "Concordância entre leituras", "tabelas": "Tabelas", "formulas": "Fórmulas", "titulos": "Títulos",
    "figura": "Figuras e fluxogramas", "busca": "Teste de busca", "paginas": "Número de páginas",
    "segunda_leitura": "Segunda leitura", "sem_texto_pdf": "Página sem texto no PDF",
}
RESULTADO = {"ok": "✅ Bateu", "info": "🔵 Resolvido sozinho", "aviso": "🟠 Precisa de você", "falha": "🔴 Precisa de você"}
NOMES_STATUS = {"rodando": "🔄 Rodando", "ok": "✅ OK", "com_erros": "⚠️ Com erros", "falhou": "❌ Falhou",
                "interrompida": "⏹️ Interrompida"}


# ------------------------------------------------------------------ utilitários
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


def em_andamento() -> bool:
    return pipeline.execucao_em_andamento(PERFIL)


def disparar(args: list[str]) -> Path:
    """Roda o pipeline em segundo plano e guarda a saída num log."""
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    log = PASTA_LOGS / f"execucao_{datetime.now():%Y%m%d_%H%M%S}.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log, "w", encoding="utf-8") as f:
        subprocess.Popen([sys.executable, "-X", "utf8", "-m", "docrag", "--perfil", str(PERFIL.pasta), *args], cwd=RAIZ,
                         stdout=f, stderr=subprocess.STDOUT, creationflags=flags,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    st.session_state["log"] = str(log)
    return log


def abrir_pasta(pasta: Path):
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(pasta)  # abre no Explorador de Arquivos
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(pasta)])
    except Exception:
        st.info(f"Abra esta pasta: `{pasta}`")


def rotulo(d: dict) -> str:
    tipo = PERFIL.tipo(d.get("tipo"))
    nome = f"{'PdC' if d.get('tipo') == 'pdc' else (tipo.rotulo if tipo else 'Documento')} {d.get('documento') or ''}".strip()
    return f"{nome} v{d.get('versao') or '?'}"


@st.cache_data(show_spinner=False, max_entries=64)
def imagem_pagina(pdf: str, pagina: int, mtime: float) -> bytes:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        img = doc[pagina - 1].render(scale=1.6).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def mostrar_pagina_pdf(doc: dict, pagina: int):
    pdf = pipeline.pasta_doc(PERFIL, doc) / "original.pdf"
    if pdf.exists() and pagina:
        st.image(imagem_pagina(str(pdf), int(pagina), pdf.stat().st_mtime), caption=f"Página {pagina} do PDF",
                 width="stretch")


def texto_pagina_final(doc: dict, pagina: int) -> str:
    arq = pipeline.pasta_doc(PERFIL, doc) / "final.md"
    if not arq.exists():
        return ""
    pags = conversores.separar_paginas(arq.read_text(encoding="utf-8"))
    return pags[pagina - 1] if 0 < pagina <= len(pags) else ""


def novos_na_entrada() -> list[str]:
    """PDFs (e ZIPs ainda não abertos) que estão na pasta de entrada e ainda não entraram na esteira."""
    conhecidos = set(ler("SELECT arquivo FROM documentos").arquivo)
    novos = [p.relative_to(PERFIL.pasta_entrada).as_posix() for p in pipeline.pdfs_na_entrada(PERFIL)]
    novos = [n for n in novos if n not in conhecidos]
    novos += [z.name for z in PERFIL.pasta_entrada.rglob("*.zip") if not z.with_suffix("").exists()]
    return [n for n in novos if n not in st.session_state.setdefault("tentados", set())]


def pendencias() -> list[dict]:
    con = conectar(PERFIL.banco)
    try:
        return pipeline.pendencias(PERFIL, con)
    finally:
        con.close()


def docs_df() -> pd.DataFrame:
    return ler("SELECT * FROM documentos ORDER BY tipo, documento, versao")


def vigentes(docs: pd.DataFrame) -> set[int]:
    return exportar.escolher_vigentes(docs.to_dict("records")) if not docs.empty else set()


# ------------------------------------------------------------------ vigia da pasta (roda a cada 10 s)
@st.fragment(run_every=10)
def vigia():
    rodando = em_andamento()
    ultima = ler("SELECT id, status FROM execucoes ORDER BY id DESC LIMIT 1")
    versao = f"{ultima.id.iloc[0]}-{ultima.status.iloc[0]}" if not ultima.empty else "0"
    anterior = st.session_state.get("versao_banco")
    st.session_state["versao_banco"] = versao
    if anterior is not None and anterior != versao:
        st.rerun(scope="app")  # uma execução começou ou terminou: atualiza todas as telas
    if st.session_state.get("automatica", True) and not rodando:
        novos = novos_na_entrada()
        if novos:
            st.session_state["tentados"].update(novos)
            disparar(["esteira"])
            st.rerun(scope="app")


vigia()

# ------------------------------------------------------------------ cabeçalho
topo = st.columns([5, 1.3])
topo[0].markdown("## docrag")
topo[0].caption(PERFIL.nome)
with topo[1]:
    with st.popover("Atualizar programa", width="stretch"):
        st.markdown("Baixa do GitHub a versão aprovada mais recente (o mesmo que `git pull`). "
                    "Seus PDFs e resultados não são tocados.")
        if st.button("Atualizar agora", type="primary"):
            r = subprocess.run(["git", "pull"], cwd=RAIZ.parent, capture_output=True, text=True)
            saida = (r.stdout + r.stderr).strip()
            if r.returncode == 0:
                st.success("Atualizado. Feche a janela preta do painel e abra o `iniciar.bat` de novo.")
            else:
                st.error("Não consegui atualizar. Mande um print desta mensagem:")
            st.code(saida or "(sem saída)", language=None)

with st.sidebar:
    st.caption("Onde os dados ficam")
    st.markdown(f"**Entrada:** `{PERFIL.pasta_entrada}`  \n**Trabalho:** `{PERFIL.pasta_trabalho}`  \n"
                f"**Saída:** `{PERFIL.pasta_saida}`  \n**Perfil:** `{PERFIL.pasta}`")
    with st.expander("Reconverter tudo do zero"):
        st.caption("Só se mudou a versão do Docling/Marker ou as opções do perfil. Demora (uns 5 min por PDF). "
                   "Correções feitas na tela são mantidas.")
        if st.button("Reconverter", disabled=em_andamento()):
            disparar(["processar", "--forcar"])
            st.rerun()

PEND = pendencias()
DOCS = docs_df()
VIG = vigentes(DOCS)
pend_ids = {p["doc"]["id"] for p in PEND}

aba_esteira, aba_fila, aba_pacote, aba_det = st.tabs(
    ["Esteira", f"Precisa de você ({len(pend_ids)})", "Pacote pronto", "Detalhes"])


# ------------------------------------------------------------------ 1. Esteira
def situacao(d: dict) -> tuple[str, str]:
    if d["id"] in pend_ids:
        return "🟠 Precisa de você", "veja a aba Precisa de você"
    if d["etapa"] in ("importado", "convertido"):
        return "⏳ Na esteira", "sendo lido e conferido"
    obs = []
    obs.append("conferido sozinho" if d.get("metadados_conferencia") == "automatica" else "conferido por você")
    trocas = ler("SELECT COUNT(*) AS n FROM verificacoes WHERE documento_id=? AND codigo='arbitro'", (d["id"],)).n[0]
    if trocas:
        obs.append(f"{trocas} página(s) usaram a segunda leitura")
    if tem(d.get("aceito_em")):
        obs.append("aceito por você")
    if d["etapa"] == "exportado":
        return ("✅ Publicado" if d["id"] in VIG else "📁 Versão antiga (fora do pacote)"), " · ".join(obs)
    return "⏳ Publicando", " · ".join(obs)


@st.fragment(run_every=5)
def manchete():
    rodando = em_andamento()
    novos = novos_na_entrada()
    na_esteira = int(DOCS.etapa.isin(["importado", "convertido"]).sum()) if not DOCS.empty else 0
    if rodando:
        st.markdown("### 🔄 Trabalhando…")
        st.write("O programa está lendo e conferindo os PDFs. Cada PDF novo leva uns 5 minutos. "
                 "Pode fechar o navegador; ele continua.")
        log = st.session_state.get("log")
        if not log:
            logs = sorted(PASTA_LOGS.glob("execucao_*.log")) if PASTA_LOGS.exists() else []
            log = str(logs[-1]) if logs else None
        if log and Path(log).exists():
            with st.expander("Ver o que está acontecendo agora"):
                linhas = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
                st.code("\n".join(linhas[-15:]) or "(começando…)", language=None)
    elif pend_ids:
        st.markdown(f"### 🟠 {len(pend_ids)} documento(s) precisam de você")
        st.write("O resto já foi conferido e publicado. Abra a aba **Precisa de você**.")
    elif DOCS.empty:
        st.markdown("### Comece por aqui")
        st.write("Coloque os PDFs (ou o ZIP baixado da CCEE) na pasta de entrada abaixo. O programa percebe sozinho.")
    else:
        pub = len(VIG)
        st.markdown("### ✅ Tudo em dia")
        st.write(f"{pub} documento(s) publicados. Nada precisa de você agora. Coloque PDFs novos na pasta de entrada "
                 "e o programa faz o resto.")
    if novos and not rodando and not st.session_state.get("automatica", True):
        st.info(f"{len(novos)} arquivo(s) novo(s) na pasta de entrada. Clique em **Verificar agora**.")

    busca = ler("SELECT detalhe FROM verificacoes WHERE codigo='busca'")
    acertos = perguntas = 0
    for d in busca.detalhe:
        try:
            j = json.loads(d)
            acertos += j.get("acertos", 0)
            perguntas += j.get("perguntas", 0)
        except (TypeError, ValueError):
            pass
    m = st.columns(4)
    m[0].metric("Na esteira", na_esteira + len(novos), help="Sendo lidos ou conferidos agora.")
    m[1].metric("Precisa de você", len(pend_ids), help="Exceções e documentos sorteados para auditoria.")
    m[2].metric("Publicados", len(VIG), help="Versão vigente de cada documento, no pacote.")
    m[3].metric("Acerto do teste de busca", f"{acertos / perguntas:.0%}" if perguntas else "–",
                help=f"{acertos} de {perguntas} frases dos documentos acharam o trecho certo entre os 3 primeiros.")


with aba_esteira:
    manchete()

    with st.container(border=True):
        st.markdown("#### Pasta de entrada (vigiada)")
        st.write("Jogue aqui os PDFs ou o ZIP baixado da CCEE, com ou sem subpastas. O programa percebe sozinho, "
                 "lê, confere e publica. Só chama você quando algo não bate.")
        st.code(str(PERFIL.pasta_entrada), language=None)
        c = st.columns([1, 1, 2.2])
        if c[0].button("Abrir a pasta", type="primary", width="stretch"):
            abrir_pasta(PERFIL.pasta_entrada)
        if c[1].button("Verificar agora", width="stretch", disabled=em_andamento()):
            disparar(["esteira"])
            st.rerun()
        c[2].toggle("Esteira automática (verifica a pasta a cada 10 segundos)", key="automatica", value=True)

    st.markdown("#### Documentos")
    if DOCS.empty:
        st.caption("Nenhum documento ainda.")
    else:
        linhas = []
        for d in DOCS.to_dict("records"):
            sit, obs = situacao(d)
            n_ch = ler("SELECT COUNT(*) AS n FROM chunks WHERE documento_id=?", (d["id"],)).n[0]
            linhas.append({"Documento": f"{rotulo(d)} – {d.get('titulo') or d['arquivo']}", "Vigência":
                           d.get("vigencia_inicio") or "–", "Situação": sit, "Observação": obs, "Trechos": int(n_ch)})
        st.dataframe(pd.DataFrame(linhas), width="stretch", hide_index=True)
        st.caption("Quer saber o que o programa fez em cada página? Veja em **Detalhes**.")


# ------------------------------------------------------------------ 2. Precisa de você
def form_aceite(doc: dict, chave: str, rotulo_botao: str, dica: str):
    with st.form(f"aceite_{chave}"):
        nota = st.text_input("O que você conferiu? (fica registrado no relatório)", placeholder=dica)
        if st.form_submit_button(rotulo_botao):
            if len(nota.strip()) < 10:
                st.error("Escreva em uma frase o que conferiu (pelo menos 10 letras).")
            else:
                gravar("UPDATE documentos SET aceito_em=?, aceito_nota=?, auditoria=CASE auditoria WHEN 'pendente' "
                       "THEN 'feita' ELSE auditoria END WHERE id=?", (agora(), nota.strip(), int(doc["id"])))
                disparar(["exportar"])
                st.rerun()


def item_metadados(it: dict):
    d = it["doc"]
    st.write(f"**Por que parou:** {it['texto']}.")
    st.caption("Confira com a capa e a tabela de revisões (página 2 do PDF). Corrija o que for preciso e confirme.")
    col_img, col_form = st.columns([1, 1])
    with col_img:
        mostrar_pagina_pdf(d, 2 if (d.get("paginas") or 1) >= 2 else 1)
    with col_form, st.form(f"meta_{d['id']}"):
        valores = {}
        for campo, nome in (("documento", "Documento"), ("titulo", "Título"), ("versao", "Versão (revisão)"),
                            ("vigencia_inicio", "Vigência a partir de (dd/mm/aaaa)"), ("ato", "Aprovado por")):
            valores[campo] = st.text_input(nome, value=d.get(campo) or "")
        if st.form_submit_button("Confirmar metadados", type="primary"):
            if valores["vigencia_inicio"] and not exportar.data(valores["vigencia_inicio"]):
                st.error("Data inválida. Use dd/mm/aaaa, por exemplo 01/10/2026.")
            else:
                novo = {**{c: d.get(c) for c in CAMPOS_METADADOS}, **{k: v.strip() or None for k, v in valores.items()}}
                falta = metadados.faltando(PERFIL, novo)
                if falta:
                    st.error(f"Faltam: {', '.join(falta)}.")
                else:
                    gravar(f"UPDATE documentos SET {', '.join(c + '=?' for c in CAMPOS_METADADOS)}, metadados_ok=1, "
                           "metadados_conferencia='pessoa', metadados_origem='painel', atualizado_em=? WHERE id=?",
                           (*[novo[c] for c in CAMPOS_METADADOS], agora(), int(d["id"])))
                    metadados.salvar_no_csv(PERFIL, {**novo, "arquivo": d["arquivo"], "sha256": d["sha256"],
                                                     "conferido": "sim"})
                    disparar(["exportar"])
                    st.rerun()


def item_qualidade(it: dict):
    d = it["doc"]
    vs = it["verificacoes"]
    por_pagina: dict = {}
    for v in vs:
        por_pagina.setdefault(v["pagina"], []).append(v)
    st.write(f"**Por que parou:** {it['texto']}. Para cada página: compare a imagem com o texto e corrija ali mesmo, "
             "ou, se estiver tudo certo, aceite o documento no fim.")
    for pagina, lista in sorted(por_pagina.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        with st.container(border=True):
            for v in lista:
                cor = "🔴" if v["nivel"] == "falha" else "🟠"
                st.markdown(f"{cor} **{NOMES_VERIF.get(v['codigo'], v['codigo'])}**"
                            + (f" · página {pagina}" if pagina else "") + f"  \n{v['mensagem']}")
            if not pagina:
                continue
            c1, c2 = st.columns(2)
            with c1:
                mostrar_pagina_pdf(d, pagina)
            with c2, st.form(f"pag_{d['id']}_{pagina}"):
                texto = st.text_area("Texto que vai para a base (pode corrigir aqui)", texto_pagina_final(d, pagina),
                                     height=420)
                if st.form_submit_button("Salvar correção e conferir de novo", type="primary",
                                         disabled=em_andamento()):
                    pipeline.corrigir_pagina(PERFIL, d, pagina, texto)
                    disparar(["esteira", "--ids", str(d["id"])])
                    st.rerun()
    form_aceite(d, f"q{d['id']}", "O texto está certo, aceitar o documento",
                "Ex.: conferi as págs. 25 a 31; são fluxogramas e os prazos estão na seção 6")


def item_auditoria(it: dict):
    d = it["doc"]
    st.write("Este documento passou em tudo sozinho. Por sorteio, uma pessoa confere algumas páginas para medir se "
             "a conferência automática continua confiável. Compare as páginas abaixo e confirme.")
    n = int(d.get("paginas") or 1)
    amostra = sorted({max(1, min(n, p)) for p in (3, n // 2, n - 1)})
    for p in amostra:
        c1, c2 = st.columns(2)
        with c1:
            mostrar_pagina_pdf(d, p)
        with c2:
            st.caption(f"Texto final da página {p}")
            st.code(texto_pagina_final(d, p), language="markdown", wrap_lines=True)
    form_aceite(d, f"a{d['id']}", "Conferi, está certo", "Ex.: conferi as págs. 3, 15 e 29 com o PDF")


with aba_fila:
    if not PEND:
        st.markdown("### Nada precisa de você agora ✅")
        st.write("Quando alguma coisa não bater (um número diferente do PDF, metadados que não conferem, um documento "
                 "sorteado para auditoria), ela aparece aqui, com a página do PDF ao lado do texto.")
    else:
        st.markdown(f"### {len(pend_ids)} documento(s) precisam de você")
        st.write("Cada item mostra a página do PDF ao lado do texto que vai para a base. Você decide: corrige ali "
                 "mesmo ou confirma que está certo. Sua decisão fica registrada no relatório do documento.")
        if em_andamento():
            st.info("Há uma conferência rodando. Os botões de corrigir voltam quando ela terminar.")
        titulos = {"metadados": "Metadados para conferir", "qualidade": "Texto para conferir",
                   "auditoria": "Sorteado para auditoria", "erro": "Erro ao ler o PDF"}
        for i, it in enumerate(PEND):
            d = it["doc"]
            with st.expander(f"{titulos[it['tipo']]} · {rotulo(d)} – {d.get('titulo') or d['arquivo']}", expanded=i == 0):
                if it["tipo"] == "metadados":
                    item_metadados(it)
                elif it["tipo"] == "qualidade":
                    item_qualidade(it)
                elif it["tipo"] == "auditoria":
                    item_auditoria(it)
                else:
                    st.error(it["texto"])
                    st.write("Se for erro do Marker, o documento segue só com o Docling. Se for do Docling, tente de "
                             "novo; se repetir, mande um print.")
                    if st.button("Tentar de novo", key=f"retry_{d['id']}", disabled=em_andamento()):
                        disparar(["processar", "--ids", str(d["id"])])
                        st.rerun()


# ------------------------------------------------------------------ 3. Pacote pronto
def arquivos(p: Path) -> list[Path]:
    return sorted(x for x in p.rglob("*.md")) if p.exists() else []


with aba_pacote:
    nb, sp = PERFIL.pasta_saida / "notebooklm", PERFIL.pasta_saida / "sharepoint"
    manifesto = PERFIL.pasta_saida / "manifesto.csv"
    if not manifesto.exists():
        st.markdown("### O pacote ainda não foi gerado")
        st.write("Ele é montado sozinho assim que o primeiro documento passa na conferência.")
    else:
        quando = datetime.fromtimestamp(manifesto.stat().st_mtime).strftime("%H:%M de %d/%m")
        st.markdown("### Pacote pronto para subir")
        st.write(f"Atualizado sozinho às {quando}, com {len(VIG)} documento(s) vigente(s). São dois formatos do mesmo "
                 "conteúdo: escolha o do lugar onde vai usar.")
        c1, c2 = st.columns(2)
        with c1, st.container(border=True):
            fn = arquivos(nb)
            st.markdown("#### NotebookLM")
            st.write("Um arquivo por documento. Cabe no limite de 50 fontes do NotebookLM.")
            st.markdown(f"**{len(fn)} arquivo(s)** em `{nb}`")
            st.caption(" · ".join(x.name for x in fn[:12]) + (" …" if len(fn) > 12 else ""))
            st.markdown("1. Clique em **Abrir a pasta**.\n2. No NotebookLM: **Adicionar fontes** e depois "
                        "**Fazer upload**.\n3. Selecione todos os arquivos (Ctrl+A) e envie.")
            if st.button("Abrir a pasta", key="abrir_nb", type="primary"):
                abrir_pasta(nb)
        with c2, st.container(border=True):
            fs = arquivos(sp)
            st.markdown("#### SharePoint + Copilot Studio")
            st.write("Um arquivo por seção, com documento, versão, vigência, seção e página no topo. Ajuda o Copilot "
                     "a citar a fonte certa.")
            st.markdown(f"**{len(fs)} arquivo(s)** em `{sp}`")
            st.markdown("1. Clique em **Abrir a pasta** e arraste tudo para a biblioteca do SharePoint.\n"
                        "2. No Copilot Studio: **Conhecimento**, **Adicionar**, **SharePoint** e cole o link da "
                        "biblioteca.\n3. Versão nova de um documento: substitua só a pasta dele.")
            if st.button("Abrir a pasta", key="abrir_sp"):
                abrir_pasta(sp)
    fora = [(d, pipeline.apto(PERFIL, d)[1]) for d in DOCS.to_dict("records") if not pipeline.apto(PERFIL, d)[0]]
    if fora:
        with st.container(border=True):
            st.markdown("#### Fora do pacote por enquanto")
            for d, motivo in fora:
                st.markdown(f"- **{rotulo(d)}**: {motivo}")
    antigas = [d for d in DOCS.to_dict("records") if d["etapa"] == "exportado" and d["id"] not in VIG]
    if antigas:
        st.caption(f"{len(antigas)} versão(ões) antiga(s) ficaram de fora, em `outras-versoes`, para a IA não "
                   "misturar regras antigas e novas.")


# ------------------------------------------------------------------ 4. Detalhes
def pagina_lado_a_lado(doc: dict, pagina: int):
    pasta = pipeline.pasta_doc(PERFIL, doc)
    cols = st.columns(3)
    with cols[0]:
        mostrar_pagina_pdf(doc, pagina)
    for col, nome in zip(cols[1:], ["final", next((c for c in PERFIL.conversores if c != doc.get("conversor_principal")),
                                                   "marker")]):
        arq = pasta / f"{nome}.md"
        col.markdown(f"**{'Texto final (vai para a base)' if nome == 'final' else 'Segunda leitura (' + nome + ')'}**")
        if arq.exists():
            pags = conversores.separar_paginas(arq.read_text(encoding="utf-8"))
            col.code(pags[pagina - 1] if pagina <= len(pags) else "(página ausente)", language="markdown",
                     wrap_lines=True)
        else:
            col.caption("(sem esta leitura)")


with aba_det:
    st.markdown("### Detalhes")
    st.write("Tudo o que o programa conferiu e decidiu, página por página. Fica guardado para consulta; não precisa "
             "olhar no dia a dia.")
    if DOCS.empty:
        st.caption("Nenhum documento ainda.")
    else:
        rot = {int(d["id"]): rotulo(d) for d in DOCS.to_dict("records")}
        f = st.columns([1.3, 1.6, 1.6, 0.8])
        escolha = f[0].selectbox("Documento", ["Todos", *rot], format_func=lambda i: rot.get(i, "Todos"))
        mostrar = f[1].selectbox("O que mostrar", ["Decisões automáticas e o que precisou de você",
                                                    "Só o que precisou de você", "Tudo, inclusive o que bateu"])
        tipos = f[2].multiselect("Verificação", sorted(set(NOMES_VERIF.values())), placeholder="Todas")
        pag = f[3].text_input("Página", placeholder="ex.: 25")
        v = ler("SELECT documento_id, pagina, codigo, nivel, mensagem FROM verificacoes ORDER BY documento_id, "
                "pagina IS NOT NULL, pagina")
        if escolha != "Todos":
            v = v[v.documento_id == escolha]
        niveis = {"Só o que precisou de você": ["aviso", "falha"],
                  "Tudo, inclusive o que bateu": ["ok", "info", "aviso", "falha"]}.get(mostrar, ["info", "aviso", "falha"])
        v = v[v.nivel.isin(niveis)]
        v = v.assign(verif=v.codigo.map(lambda c: NOMES_VERIF.get(c, c)))
        if tipos:
            v = v[v.verif.isin(tipos)]
        if pag.strip().isdigit():
            v = v[v.pagina == int(pag)]
        meta = [{"Documento": rot[int(d["id"])], "Página": "–", "O que aconteceu": "Metadados",
                 "Em palavras simples": (f"Versão {d.get('versao')}, vigência {d.get('vigencia_inicio')}: "
                                         + ("capa, tabela de revisões e nome do arquivo batem."
                                            if d.get("metadados_conferencia") == "automatica" else
                                            d.get("metadados_motivo") or "conferidos por você.")),
                 "Resultado": ("🔵 Confirmado sozinho" if d.get("metadados_conferencia") == "automatica" else
                               "✅ Conferido por você" if d.get("metadados_ok") else "🟠 Precisa de você")}
                for d in DOCS.to_dict("records") if escolha in ("Todos", d["id"])]
        tabela = pd.DataFrame(meta + [{"Documento": rot.get(int(r.documento_id), "?"),
                                       "Página": "–" if pd.isna(r.pagina) else str(int(r.pagina)),
                                       "O que aconteceu": r.verif, "Em palavras simples": r.mensagem,
                                       "Resultado": RESULTADO.get(r.nivel, r.nivel)} for r in v.itertuples()])
        if tipos:
            tabela = tabela[tabela["O que aconteceu"].isin(tipos)]
        st.dataframe(tabela, width="stretch", hide_index=True, height=420)

        with st.expander("Comparar uma página lado a lado (imagem do PDF, texto final, segunda leitura)"):
            c = st.columns(2)
            doc_id = c[0].selectbox("Documento ", list(rot), format_func=rot.get, key="cmp_doc")
            d = DOCS[DOCS.id == doc_id].iloc[0].to_dict()
            p = c[1].number_input("Página ", 1, int(d.get("paginas") or 1), 1, key="cmp_pag")
            pagina_lado_a_lado(d, int(p))

        with st.expander("Metadados de todos os documentos (editar)"):
            tab = DOCS.copy()
            tab[CAMPOS_METADADOS] = tab[CAMPOS_METADADOS].fillna("")
            tab["conferido"] = tab.metadados_ok.astype(bool)
            colunas = ["id", "arquivo", *CAMPOS_METADADOS, "conferido"]
            editado = st.data_editor(tab[colunas], width="stretch", hide_index=True, disabled=["id", "arquivo"],
                                     key=f"editor_docs_{st.session_state.get('versao_banco')}")
            if st.button("Salvar metadados"):
                for _, linha in editado.iterrows():
                    dd = {c: (linha[c] if tem(linha[c]) else None) for c in CAMPOS_METADADOS}
                    conferido = bool(linha.conferido) and not metadados.faltando(PERFIL, dd) and \
                        all(not dd[c] or exportar.data(dd[c]) for c in ("vigencia_inicio", "vigencia_fim"))
                    gravar(f"UPDATE documentos SET {', '.join(c + '=?' for c in CAMPOS_METADADOS)}, "
                           "metadados_conferencia=CASE WHEN ?=1 AND metadados_ok=1 THEN metadados_conferencia "
                           "WHEN ?=1 THEN 'pessoa' END, metadados_ok=?, metadados_origem='painel', atualizado_em=? "
                           "WHERE id=?", (*[dd[c] for c in CAMPOS_METADADOS], int(conferido), int(conferido),
                                          int(conferido), agora(), int(linha.id)))
                    orig = tab[tab.id == linha.id].iloc[0]
                    metadados.salvar_no_csv(PERFIL, {**dd, "arquivo": orig.arquivo, "sha256": orig.sha256,
                                                     "conferido": "sim" if conferido else ""})
                disparar(["exportar"])
                st.success("Salvo. O pacote vai ser atualizado.")

        with st.expander("Trechos (chunks) de um documento"):
            doc_id = st.selectbox("Documento  ", list(rot), format_func=rot.get, key="ch_doc")
            d = DOCS[DOCS.id == doc_id].iloc[0].to_dict()
            ch = ler("SELECT * FROM chunks WHERE documento_id=? ORDER BY ordem", (int(doc_id),))
            if not ch.empty:
                st.dataframe(ch[["ordem", "secao", "pagina_inicio", "pagina_fim", "palavras"]], width="stretch",
                             hide_index=True, column_config={"ordem": "Nº", "secao": "Seção", "pagina_inicio":
                                                             "Pág. início", "pagina_fim": "Pág. fim",
                                                             "palavras": "Palavras"})
                n = st.number_input("Ver o trecho nº", 1, len(ch), 1)
                st.code(exportar.texto_chunk(PERFIL, d, ch[ch.ordem == n].iloc[0].to_dict()), language="markdown",
                        wrap_lines=True)

    with st.expander("Glossário"):
        st.caption(f"Siglas e variáveis tiradas dos documentos, mais as de `{PERFIL.arquivo('glossario.csv')}` "
                   "(que vencem as automáticas).")
        fontes = []
        for d in DOCS.to_dict("records"):
            fm = pipeline.pasta_doc(PERFIL, d) / "final.md"
            if d["etapa"] in ("verificado", "exportado") and fm.exists():
                fontes.append((exportar.rotulo_documento(PERFIL, d), fm.read_text(encoding="utf-8")))
        ent = glossario.montar(PERFIL, fontes)
        if ent:
            st.dataframe(pd.DataFrame([e.__dict__ for e in ent]), width="stretch", hide_index=True)

    with st.expander("Perguntas de teste"):
        st.caption("Perguntas com a resposta certa, para medir a base. Em **trechos** cole frases exatas do documento "
                   "(várias separadas por `||`).")
        arq = PERFIL.arquivo("perguntas.csv")
        perg = pd.read_csv(arq, dtype=str, keep_default_na=False) if arq.exists() else \
            pd.DataFrame(columns=avaliacao.COLUNAS_PERGUNTAS)
        perg = perg.reindex(columns=avaliacao.COLUNAS_PERGUNTAS, fill_value="")
        novo = st.data_editor(perg, num_rows="dynamic", width="stretch", hide_index=True, key="editor_perg")
        c = st.columns(2)
        if c[0].button("Salvar perguntas"):
            novo.to_csv(arq, index=False, encoding="utf-8-sig")
            st.success("Salvo.")
        if c[1].button("Rodar o teste", disabled=em_andamento()):
            disparar(["avaliar"])
            st.rerun()
        res = ler("SELECT * FROM avaliacoes WHERE em=(SELECT MAX(em) FROM avaliacoes)")
        if not res.empty:
            achou = int((res.resultado == "achou").sum())
            st.metric("Achou o trecho certo entre os 5 primeiros", f"{achou}/{len(res)}")
            st.dataframe(res[["pergunta_id", "pergunta", "resultado", "posicao", "chunk", "detalhe"]], width="stretch",
                         hide_index=True)
        arq_c = PERFIL.arquivo("resultados_copilot.csv")
        if arq_c.exists():
            st.markdown("**Respostas do Copilot / NotebookLM (preencha à mão)**")
            rc = pd.read_csv(arq_c, dtype=str, keep_default_na=False).reindex(columns=avaliacao.COLUNAS_COPILOT,
                                                                              fill_value="")
            rc_novo = st.data_editor(rc, width="stretch", hide_index=True, key="editor_copilot",
                                     disabled=["id", "pergunta", "resposta_esperada"])
            if st.button("Salvar respostas"):
                rc_novo.to_csv(arq_c, index=False, encoding="utf-8-sig")
                st.success("Salvo.")

    with st.expander("Registro de execuções e eventos (técnico)"):
        e = ler("SELECT id, comando, inicio, fim, status, resumo FROM execucoes ORDER BY id DESC LIMIT 100")
        if not e.empty:
            st.dataframe(e.assign(status=e.status.map(lambda s: NOMES_STATUS.get(s, s))), width="stretch",
                         hide_index=True)
        niveis_ev = st.multiselect("Eventos", ["info", "aviso", "erro"], default=["aviso", "erro"])
        ev = ler("SELECT e.em, e.execucao_id AS execucao, e.nivel, d.arquivo, e.mensagem FROM eventos e "
                 "LEFT JOIN documentos d ON d.id = e.documento_id ORDER BY e.id DESC LIMIT 1000")
        if not ev.empty:
            st.dataframe(ev[ev.nivel.isin(niveis_ev)], width="stretch", hide_index=True)
