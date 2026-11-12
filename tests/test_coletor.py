"""Testes do coletor com dados no formato real do site (sem acessar a internet)."""
import hashlib
from datetime import date

import pytest

from ccee import db, descoberta, download, pipeline

HTML_PDCS = """
<ul><li><a class="nav-link active" href="#v-pills-1">Módulo 1 - Agentes</a></li>
<li><a class="nav-link" href="#v-pills-9">Procedimentos Provisórios</a></li>
<li><a class="nav-link" href="#v-pills-10">Procedimentos Futuros</a></li></ul>
<div class="tab-content">
 <div class="tab-pane" id="v-pills-1">
  <dl><dt><strong>Submódulo 1.1 - Adesão à CCEE - Versão 10.0 - 28/04/2026</strong></dt>
   <dd><p>Estabelece requisitos... <a href="http://www2.aneel.gov.br/x.pdf">Convenção Arbitral</a></p>
    <a href=" https://www.ccee.org.br/o/ccee/documentos/CCEE_058253 "> Acesse o PdC </a>
    <a href="https://www.ccee.org.br/documents/80415/1/Anexo.docx">Termo de Adesão</a></dd></dl>
  <dl><dt><strong>Submódulo 1.6 - Comercialização Varejista - Versão 7.0 - 01/10/2026</strong></dt>
   <dd><a href="https://www.ccee.org.br/documents/80415/29314541/1.6_-_Comercializacao_varejista_v6.0.pdf/5095ed44">Acesse o PdC</a></dd></dl>
 </div>
 <div class="tab-pane" id="v-pills-9">
  <dl><dt><strong>Procedimentos de Comercialização Provisório - 1.1 - Adesão à CCEE, 3.2 - Contratos do ACR - Versão X.0 - 01/01/2026</strong></dt>
   <dd><a href="https://www.ccee.org.br/documents/80415/30995071/Nova%20forma%20de%20rateio.zip/240f">Acesse o PdC</a></dd></dl>
 </div>
 <div class="tab-pane" id="v-pills-10">
  <dl><dt><strong>Submódulo 1.8 – Comercialização varejista - modelo simplificado - Versão 2.0 - 01/01/2027</strong></dt>
   <dd><a href="https://www.ccee.org.br/o/ccee/documentos/CCEE_1523401">Acesse o PdC</a></dd></dl>
 </div>
</div>
"""


def item_regra(fid, nome, versao, de, ate, publ, tipo="Caderno de Regras de Comercialização", ext="PDF", hash_="h"):
    return {
        "fileEntryID": fid, "nomeDocumento": nome, "nomeComplementar": nome.split("_")[0],
        "versao": versao, "periodoDe": de, "periodoAte": ate, "categoryJournal": "Vigente",
        "nomeDocumentoList": tipo, "tipoDocumentalList": "Caderno de Regras", "extension": ext,
        "instrumentoNome": "REN 1.146", "instrumentoUrl": "https://aneel/x.pdf",
        "url": f"https://www.ccee.org.br/documents/80415/1/{nome}/abc", "hash": hash_,
        "publishDateFMT": publ,
    }


REGRAS = [
    item_regra(1, "09 - Encargos_2026.6.4_JUN.pdf", "2026.6.4.", "06/2026", "12/2026", "31/08/2026"),
    item_regra(2, "09 - Encargos_2026.1.4_JAN.pdf", "2026.1.4.", "01/2026", "05/2026", "31/08/2026"),
    item_regra(3, "09 - Encargos_2026.6.3_JUN.pdf", "2026.6.3", "06/2026", "12/2026", "18/06/2026"),
    item_regra(4, "00 - Preço de Liquidação das Diferenças_2026.1.0_JAN.pdf", "2026.1.0", "01/2026", "12/2026", "22/12/2025"),
    item_regra(5, "00 - PLD_2025.1.0-JAN.pdf", "2025.1.0", "01/2025", "12/2025", "20/12/2024"),
    item_regra(6, "Layout download.xlsx", None, None, None, "01/01/2025", tipo="Layouts: Download de Dados", ext="XLSX"),
]


class ClienteFalso:
    """Imita o site: paginação da API das Regras, página dos PdCs e downloads."""

    def __init__(self, paginas, html=HTML_PDCS, arquivos=None):
        self.paginas, self.html, self.arquivos = paginas, html, arquivos or {}
        self.chamadas = []

    class Resp:
        def __init__(self, json_=None, text="", content=b"", headers=None):
            self._json, self.text, self.content, self.headers = json_, text, content, headers or {}

        def json(self):
            return self._json

    def post(self, url, data):
        n = int(next(v for k, v in data.items() if k.endswith("numberPage")))
        self.chamadas.append(("post", n))
        return self.Resp(json_={"total": 3, "results": self.paginas[n - 1] if n <= len(self.paginas) else []})

    def get(self, url, **kw):
        self.chamadas.append(("get", url))
        if "procedimentos" in url:
            return self.Resp(text=self.html)
        for chave, (conteudo, headers) in self.arquivos.items():
            if chave in url:
                return self.Resp(content=conteudo, headers=headers)
        return self.Resp(content=b"%PDF-1.7 conteudo de teste " + url.encode(), headers={})


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "BANCO", tmp_path / "pipeline.db")
    monkeypatch.setattr(pipeline.config, "TRAVA", tmp_path / ".lock")
    monkeypatch.setattr(download.config, "PASTA_PDF", tmp_path / "pdf")
    return tmp_path


# ------------------------------------------------------------------ Regras

def test_paginacao_ate_pagina_vazia_e_sem_duplicados():
    cli = ClienteFalso([REGRAS[:3], REGRAS[2:], []])
    itens = descoberta.listar_regras_brutas(cli)
    assert sorted(i["fileEntryID"] for i in itens) == [1, 2, 3, 4, 5, 6]
    assert cli.chamadas == [("post", 1), ("post", 2), ("post", 3)]


def test_selecao_vigente_por_mes_e_publicacao_mais_recente():
    docs = [descoberta.normalizar_regra(i) for i in REGRAS]
    descoberta.selecionar_regras_vigentes(docs, hoje=date(2026, 11, 12))
    sel = {d["nome_arquivo"] for d in docs if d["selecionado"]}
    assert sel == {"09 - Encargos_2026.6.4_JUN.pdf", "00 - Preço de Liquidação das Diferenças_2026.1.0_JAN.pdf"}
    encargos = next(d for d in docs if d["id"] == "regras:1")
    assert encargos["versao"] == "2026.6.4"           # ponto final removido
    assert "2 versões cobrem 2026-11" in encargos["alerta"]
    layout = next(d for d in docs if d["id"] == "regras:6")
    assert layout["selecionado"] == 0 and "fora do escopo" in layout["motivo_selecao"]


def test_selecao_em_marco_escolhe_versao_de_janeiro():
    docs = [descoberta.normalizar_regra(i) for i in REGRAS]
    descoberta.selecionar_regras_vigentes(docs, hoje=date(2026, 3, 1))
    assert {d["id"] for d in docs if d["selecionado"] and d["codigo"] == "09"} == {"regras:2"}


# -------------------------------------------------------------------- PdCs

def test_parse_pdcs_titulos_versoes_e_abas():
    docs = {d["id"]: d for d in descoberta.parse_pdcs(HTML_PDCS)}
    assert set(docs) >= {"pdc:1.1", "pdc:1.6", "pdc:1.8:futuro"}
    d11 = docs["pdc:1.1"]
    assert d11["url"] == "https://www.ccee.org.br/o/ccee/documentos/CCEE_058253"
    assert d11["versao"] == "10.0" and d11["vigencia_inicio"] == "2026-04-28" and d11["selecionado"] == 1
    assert "v6.0" in docs["pdc:1.6"]["alerta"]                       # título 7.0 x arquivo 6.0
    assert docs["pdc:1.8:futuro"]["selecionado"] == 0
    prov = [d for d in docs.values() if d["grupo"] == "Procedimentos Provisórios"]
    assert len(prov) == 1 and prov[0]["extensao"] == "ZIP" and prov[0]["selecionado"] == 0


# --------------------------------------------------------- Download e banco

def test_url_segura_codifica_espacos_e_acentos():
    u = download.url_segura("https://www.ccee.org.br/documents/80415/1/09 - Encargos_2026.pdf/abc")
    assert u == "https://www.ccee.org.br/documents/80415/1/09%20-%20Encargos_2026.pdf/abc"
    assert download.url_segura(u) == u  # não codifica duas vezes


def test_download_rejeita_html_no_lugar_de_pdf(banco):
    cli = ClienteFalso([[]], arquivos={"CCEE_058253": (b"<html>erro</html>", {"content-type": "text/html"})})
    with pytest.raises(ValueError, match="não parece PDF"):
        download.baixar(cli, {"url": "https://x/o/ccee/documentos/CCEE_058253", "fonte": "pdc",
                              "codigo": "1.1", "extensao": "PDF"})


def test_fluxo_completo_retoma_e_detecta_mudanca(banco, monkeypatch):
    pdf = b"%PDF-1.7 adesao"
    arquivos = {"CCEE_058253": (pdf, {"content-disposition": 'filename="1.1_-_adesao_a_ccee_v10.0.pdf"'})}
    cli = ClienteFalso([REGRAS, []], arquivos=arquivos)
    monkeypatch.setattr(pipeline, "Cliente", lambda: cli)  # usa o site falso
    monkeypatch.setattr(pipeline, "descobrir_regras",
                        lambda c: descoberta.descobrir_regras(c, hoje=date(2026, 11, 12)))
    # 1ª rodada: só descobre
    assert pipeline.executar("descobrir", log=lambda m: None) == 0
    con = db.conectar()
    sel = con.execute("SELECT COUNT(*) FROM documentos WHERE selecionado=1").fetchone()[0]
    assert sel == 2 + 2  # 2 cadernos vigentes em nov/2026 + 2 PdCs dos módulos
    # baixa com limite 1 e depois o resto: deve retomar sem repetir
    pipeline.executar("baixar", limite=1, log=lambda m: None)
    assert con.execute("SELECT COUNT(*) FROM documentos WHERE etapa='baixado'").fetchone()[0] == 1
    pipeline.executar("baixar", log=lambda m: None)
    assert con.execute("SELECT COUNT(*) FROM documentos WHERE selecionado=1 AND etapa!='baixado'").fetchone()[0] == 0
    linha = con.execute("SELECT * FROM documentos WHERE id='pdc:1.1'").fetchone()
    assert linha["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert linha["caminho_local"].endswith("1.1_-_adesao_a_ccee_v10.0.pdf")

    # Site publica novo arquivo para o caderno 09 (hash muda): volta para 'descoberto'
    novo = [dict(i) for i in REGRAS]
    novo[0]["hash"] = "outro"
    cli.paginas = [novo, []]
    pipeline.executar("descobrir", log=lambda m: None)
    assert con.execute("SELECT etapa FROM documentos WHERE id='regras:1'").fetchone()[0] == "descoberto"
    assert con.execute("SELECT COUNT(*) FROM eventos WHERE nivel='mudanca'").fetchone()[0] == 1
    execs = [r["status"] for r in con.execute("SELECT status FROM execucoes ORDER BY id")]
    assert execs == ["ok", "ok", "ok", "ok"]


def test_execucao_interrompida_fica_registrada(banco, monkeypatch):
    con = db.conectar()
    db.iniciar_execucao(con, "tudo")  # simula queda sem finalizar
    cli = ClienteFalso([[]])
    monkeypatch.setattr(pipeline, "Cliente", lambda: cli)
    pipeline.executar("descobrir", log=lambda m: None)
    status = [r["status"] for r in con.execute("SELECT status FROM execucoes ORDER BY id")]
    assert status[0] == "interrompida"
