"""Testes da importação dos PDFs baixados à mão (sem acessar a internet)."""
from pathlib import Path

import pytest

from ccee import db, importar, pipeline


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "BANCO", tmp_path / "pipeline.db")
    monkeypatch.setattr(pipeline.config, "TRAVA", tmp_path / ".lock")
    monkeypatch.setattr(importar.config, "PASTA_PDF", tmp_path / "pdf")
    return tmp_path


def test_importar_pdfs_baixados_a_mao(banco, tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "05 - Contratos_2026.6.4_JUN.pdf").write_bytes(b"%PDF-1.7 caderno 05")
    (entrada / "1.6_-_Comercializacao_varejista_v6.0.pdf").write_bytes(b"%PDF-1.7 pdc 1.6")
    (entrada / "pagina.pdf").write_bytes(b"<html>bloqueado</html>")  # página de erro salva como PDF
    con = db.conectar()
    eid = db.iniciar_execucao(con, "importar")
    c = importar.importar_pasta(con, eid, entrada, log=lambda m: None)
    assert c["importados_novo"] == 2 and c["erros"] == 1
    docs = {r["id"]: dict(r) for r in con.execute("SELECT * FROM documentos")}
    r05, p16 = docs["regras:05:importado"], docs["pdc:1.6:importado"]
    assert (r05["versao"], r05["etapa"], r05["selecionado"]) == ("2026.6.4", "baixado", 1)
    assert p16["titulo"] == "Submódulo 1.6 - Comercializacao varejista" and p16["versao"] == "6.0"
    assert Path(p16["caminho_local"]).read_bytes() == b"%PDF-1.7 pdc 1.6"
    # importar de novo o mesmo arquivo não duplica; versão nova substitui e guarda as duas no disco
    assert importar.importar_pasta(con, eid, entrada, log=lambda m: None)["importados_igual"] == 2
    (entrada / "1.6_-_Comercializacao_varejista_v6.0.pdf").write_bytes(b"%PDF-1.7 pdc 1.6 corrigido")
    assert importar.importar_pasta(con, eid, entrada, log=lambda m: None)["importados_alterado"] == 1
    assert len(list((tmp_path / "pdf" / "pdc" / "1.6").glob("*.pdf"))) == 2


def test_importar_zip_do_botao_download_das_regras(banco, tmp_path):
    import zipfile
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    with zipfile.ZipFile(entrada / "Regras.zip", "w") as z:
        z.writestr("regras/05 - Contratos_2026.6.4_JUN.pdf", b"%PDF-1.7 caderno 05")
        z.writestr("regras/09 - Encargos_2026.6.4_JUN.pdf", b"%PDF-1.7 caderno 09")
        z.writestr("leia-me.txt", b"ignorado")
    con = db.conectar()
    eid = db.iniciar_execucao(con, "importar")
    c = importar.importar_pasta(con, eid, entrada, log=lambda m: None)
    assert c["importados_novo"] == 2 and not c["erros"]
    ids = {r["id"] for r in con.execute("SELECT id FROM documentos")}
    assert ids == {"regras:05:importado", "regras:09:importado"}


def test_identifica_nomes_como_o_site_publica():
    assert importar.identificar("09 - Encargos_2026.6.4_JUN.pdf")["codigo"] == "09"
    assert importar.identificar("1.6_-_Comercializacao_varejista_v6.0.pdf")["versao"] == "6.0"
    assert importar.identificar("Submódulo 3.8 - Mecanismo de Venda de Excedentes - v8.0.pdf")["codigo"] == "3.8"
    assert importar.identificar("documento qualquer.pdf")["fonte"] == "outros"


def test_comando_importar_e_execucao_interrompida(banco):
    con = db.conectar()
    db.iniciar_execucao(con, "importar")  # simula queda sem finalizar
    entrada = banco / "entrada"
    entrada.mkdir()
    (entrada / "09 - Encargos_2026.6.4_JUN.pdf").write_bytes(b"%PDF-1.7 x")
    assert pipeline.executar("importar", pasta=entrada, log=lambda m: None) == 0
    status = [r["status"] for r in con.execute("SELECT status FROM execucoes ORDER BY id")]
    assert status == ["interrompida", "ok"]
    assert "regras  baixado" in pipeline.status_texto()
