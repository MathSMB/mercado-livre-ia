"""Monitor do DOU com um ZIP no formato do INLABS (sem internet)."""
import io
import zipfile
from datetime import date

import pytest

from ccee import config, db, monitor_dou

XML = """<xml><article id="1" name="REN" idMateria="{id}" pubName="DO1" artType="{tipo}"
 artCategory="Ministério de Minas e Energia/Agência Nacional de Energia Elétrica/Diretoria"
 pdfPage="http://pesquisa.in.gov.br/x?pagina=80"><body>
<Identifica><![CDATA[{ident}]]></Identifica><Ementa><![CDATA[{ementa}]]></Ementa>
<Texto><![CDATA[<p>{texto}</p>]]></Texto></body></article></xml>"""


def zip_edicao(*materias):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, m in enumerate(materias):
            z.writestr(f"{i}.xml", XML.format(**m))
    return buf.getvalue()


class InlabsFalso:
    def __init__(self, edicoes):
        self.edicoes, self.pedidos = edicoes, []

    def entrar(self):
        pass

    def baixar_zip(self, dia, secao):
        self.pedidos.append((dia, secao))
        return self.edicoes.get((dia, secao))


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "BANCO", tmp_path / "pipeline.db")
    monkeypatch.setattr(monitor_dou.time, "sleep", lambda s: None)


def test_encontra_ato_da_aneel_e_nao_repete(banco):
    seg = date(2026, 11, 9)
    edicoes = {(seg, "DO1"): zip_edicao(
        {"id": "111", "tipo": "Resolução Normativa", "ident": "RESOLUÇÃO NORMATIVA ANEEL Nº 1.180",
         "ementa": "Aprova a versão 2027.1.0 das Regras de Comercialização de energia elétrica.", "texto": "..."},
        {"id": "222", "tipo": "Despacho", "ident": "DESPACHO Nº 3.000",
         "ementa": "", "texto": "Aprovar o Submódulo 1.6 dos Procedimentos de Comercializa&ccedil;&atilde;o."},
        {"id": "333", "tipo": "Portaria", "ident": "PORTARIA Nº 9", "ementa": "Outro assunto.", "texto": "nada"},
    )}
    cli = InlabsFalso(edicoes)
    assert monitor_dou.executar(hoje=date(2026, 11, 10), cliente=cli, log=lambda m: None) == 0
    con = db.conectar()
    av = {r["id"]: dict(r) for r in con.execute("SELECT * FROM dou_avisos")}
    assert set(av) == {"111", "222"}
    assert av["111"]["assunto"] == "Regras" and av["222"]["assunto"] == "PdC"
    assert "Submódulo 1.6" in av["222"]["trecho"]
    # fim de semana não é pedido; segunda rodada só lê o que falta
    assert all(d.weekday() < 5 for d, _ in cli.pedidos)
    cli2 = InlabsFalso(edicoes)
    monitor_dou.executar(hoje=date(2026, 11, 11), cliente=cli2, log=lambda m: None)
    assert {d for d, _ in cli2.pedidos} == {date(2026, 11, 11)}
    assert con.execute("SELECT COUNT(*) FROM dou_avisos").fetchone()[0] == 2


def test_sem_credenciais_explica_o_que_fazer(banco, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INLABS_CREDENCIAIS", tmp_path / "inlabs.txt")
    monkeypatch.delenv("INLABS_EMAIL", raising=False)
    monkeypatch.delenv("INLABS_SENHA", raising=False)
    with pytest.raises(monitor_dou.SemCredenciais):
        monitor_dou.credenciais()
    (tmp_path / "inlabs.txt").write_text("email=a@b.com\nsenha=x=y\n", encoding="utf-8")
    assert monitor_dou.credenciais() == ("a@b.com", "x=y")
