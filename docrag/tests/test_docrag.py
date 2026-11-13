"""Testes do docrag sem baixar modelos: os conversores são simulados com Markdown escrito à mão."""
from __future__ import annotations

import csv
import shutil
from datetime import date
from pathlib import Path

import pytest

from docrag import avaliacao, chunking, conversores, exportar, glossario, perfil as perfil_mod, pipeline, qualidade
from docrag import texto as tx
from docrag.banco import aberto

AQUI = Path(__file__).parent
PDF = AQUI / "dados" / "pdc-exemplo.pdf"
PERFIL_CCEE = AQUI.parent / "perfis" / "ccee"

REPETIDO_1 = ("Este submódulo estabelece as regras de exemplo para o Mecanismo de Compensação de Sobras e Déficits "
              "(MCSD) e para a Câmara de Comercialização de Energia Elétrica (CCEE). ")
PRAZO = ("O agente deve enviar os dados até o 5º dia útil do mês subsequente, com tolerância de 2,5% sobre o "
         "montante de 1.250,75 MWh. ")
RESP = "Cabe à CCEE publicar o resultado em até 10 dias. "

PAG1 = f"""# Procedimento de Comercialização

## Submódulo 9.9 – Exemplo de Teste

Versão 2.0 – Vigência: 01/01/2026

Aprovado pela REN ANEEL nº 1.234/2025

## 1. OBJETIVO

{REPETIDO_1 * 3}

## 2. PREMISSAS

## 2.1 Prazos

{PRAZO * 4}

## 2.2 Responsabilidades

{RESP * 5}"""

PAG2 = """## 3. ATIVIDADES

A tabela a seguir resume as atividades e prazos.

| Atividade    | Responsável   | Prazo   |
|--------------|---------------|---------|
| Enviar dados | Agente        | MS+5du  |
| Validar      | CCEE          | MS+8du  |
| Publicar     | CCEE          | MS+10du |

## 3.1 Cálculo

O montante é dado por VTOT = Σ (G × FA) para cada perfil a no mês m.

## 4. GLOSSÁRIO

| Variável   | Descrição                    |
|------------|------------------------------|
| VTOT       | Valor total do agente no mês |
| FA         | Fator de ajuste              |"""


def conv(nome, paginas):
    return conversores.Conversao(nome, "teste", paginas)


@pytest.fixture
def info():
    return conversores.ler_pdf(PDF)


@pytest.fixture
def perfil(tmp_path):
    destino = tmp_path / "perfil"
    shutil.copytree(PERFIL_CCEE, destino)
    toml = destino / "perfil.toml"
    t = toml.read_text(encoding="utf-8")
    for chave in ("entrada", "trabalho", "saida"):
        t = t.replace(f'"../../dados/{chave}"', f'"{(tmp_path / chave).as_posix()}"')
    toml.write_text(t, encoding="utf-8")
    p = perfil_mod.carregar(destino)
    assert p.pasta_entrada == tmp_path / "entrada"
    return p


# --------------------------------------------------------------------------- páginas
def test_juntar_e_separar_paginas_ida_e_volta():
    pags = ["um", "", "três\n\ncom parágrafo"]
    assert conversores.separar_paginas(conversores.juntar_paginas(pags)) == pags


def test_separar_paginas_marker():
    texto = "\n\n{0}" + "-" * 48 + "\n\nPrimeira\n\n{1}" + "-" * 48 + "\n\nSegunda"
    assert conversores.separar_paginas_marker(texto, 3) == ["Primeira", "Segunda", ""]


def test_ler_pdf(info):
    assert info.n_paginas == 2
    assert "1.250,75" in info.textos[0]


# --------------------------------------------------------------------------- portão
def test_portao_aprova_conversoes_fieis(info, perfil):
    rel = qualidade.avaliar(info, conv("docling", [PAG1, PAG2]), conv("marker", [PAG1, PAG2]), perfil.limiares)
    assert rel.status == "ok", [v.mensagem for v in rel.problemas()]


def test_portao_pega_numero_trocado(info, perfil):
    errado = PAG1.replace("1.250,75", "1.205,75", 1)
    rel = qualidade.avaliar(info, conv("docling", [errado, PAG2]), conv("marker", [PAG1, PAG2]), perfil.limiares)
    nums = [v for v in rel.problemas() if v.codigo == "numeros"]
    assert nums and nums[0].pagina == 1
    assert "1.205,75" in nums[0].detalhe["a_mais"] and "1.250,75" in nums[0].detalhe["faltando"]
    assert rel.status == "falha"


def test_portao_pega_paragrafo_perdido(info, perfil):
    sem_resp = PAG1.split("## 2.2")[0]
    rel = qualidade.avaliar(info, conv("docling", [sem_resp, PAG2]), conv("marker", [PAG1, PAG2]), perfil.limiares)
    cob = next(v for v in rel.verificacoes if v.codigo == "cobertura" and v.pagina == 1)
    assert cob.nivel == "falha"
    assert "responsabilidades" in cob.detalhe["faltando"]


def test_portao_pega_tabela_que_virou_texto(info, perfil):
    sem_tabela = PAG2.replace("|--------------|---------------|---------|\n", "")
    rel = qualidade.avaliar(info, conv("docling", [PAG1, sem_tabela]), conv("marker", [PAG1, PAG2]), perfil.limiares)
    assert any(v.codigo == "tabelas" and v.pagina == 2 for v in rel.problemas())


def test_portao_pega_pagina_faltando(info, perfil):
    rel = qualidade.avaliar(info, conv("docling", [PAG1]), conv("marker", [PAG1, PAG2]), perfil.limiares)
    assert any(v.codigo == "paginas" and v.nivel == "falha" for v in rel.verificacoes)


def test_portao_compara_formulas(info, perfil):
    a = PAG2 + "\n\n$$V_{TOT} = \\sum_a G_a \\times FA$$"
    b = PAG2 + "\n\n$$V_{TOT} = \\sum_a G_a + FA$$"
    rel = qualidade.avaliar(info, conv("docling", [PAG1, a]), conv("marker", [PAG1, b]), perfil.limiares)
    assert any(v.codigo == "formulas" for v in rel.problemas())


def test_saltos_numeracao():
    assert qualidade.saltos_numeracao(["1", "2", "2.1", "2.3", "3"]) == ["2.1 → 2.3"]
    assert qualidade.saltos_numeracao(["1", "2", "2.1", "2.2", "3", "3.1"]) == []


def test_latex_achatado_bate_com_pdf():
    assert "VTOT" in tx.latex_para_texto("V_{TOT}").replace(" ", "")


# --------------------------------------------------------------------------- chunking
def test_chunks_por_secao_com_caminho_e_pagina():
    md = conversores.juntar_paginas([PAG1, PAG2])
    cs = chunking.chunks(md, max_palavras=700, min_palavras=20)
    rotulos = [c.secao for c in cs]
    assert any(r.startswith("2.1 Prazos") for r in rotulos)
    prazos = next(c for c in cs if c.secao.startswith("2.1"))
    assert prazos.caminho == ["2 PREMISSAS", "2.1 Prazos"]
    assert prazos.pagina_inicio == 1
    atividades = next(c for c in cs if c.numero == "3")
    assert atividades.pagina_inicio == 2 and "| Enviar dados" in atividades.texto


def test_chunk_nao_corta_tabela_e_repete_cabecalho():
    linhas = "\n".join(f"| item {i} | descrição longa do item número {i} da tabela |" for i in range(200))
    md = f"## 1. Tabela grande\n\n| Item | Descrição |\n|---|---|\n{linhas}"
    cs = chunking.chunks(md, max_palavras=150, min_palavras=10)
    assert len(cs) > 3
    for c in cs:
        assert c.texto.count("| Item | Descrição |") == 1 or c.parte == 1
        assert all(l.startswith("|") or l.startswith("#") or not l for l in c.texto.splitlines())
    assert sum(c.texto.count("| item ") for c in cs) == 200


def test_chunk_nao_corta_formula():
    md = "## 1. Fórmula\n\n" + ("texto " * 100) + "\n\n$$\nA = B\n+ C\n$$\n\n" + ("mais " * 100)
    cs = chunking.chunks(md, max_palavras=120, min_palavras=10)
    assert any("$$\nA = B\n+ C\n$$" in c.texto for c in cs)


def test_secao_so_com_titulo_junta_com_filha():
    md = conversores.juntar_paginas([PAG1, PAG2])
    cs = chunking.chunks(md, max_palavras=700, min_palavras=20)
    assert not any(c.secao == "2 PREMISSAS" for c in cs)  # só título: foi junto com 2.1


def test_tabela_atravessando_pagina_fica_inteira():
    md = conversores.juntar_paginas(["## 1. X\n\n| a | b |\n|---|---|\n| 1 | 2 |", "| 3 | 4 |\n\nDepois."])
    blocos = chunking._blocos(md)
    tabela = next(b for b in blocos if b.tipo == "tabela")
    assert "| 3 | 4 |" in tabela.texto


# --------------------------------------------------------------------------- glossário
def test_glossario_siglas_e_variaveis():
    ent = glossario.siglas_do_texto(PAG1, "PdC 9.9") + glossario.tabelas_do_texto(PAG2, "PdC 9.9")
    d = {e.termo: e for e in ent}
    assert d["MCSD"].definicao == "Mecanismo de Compensação de Sobras e Déficits"
    assert d["CCEE"].definicao == "Câmara de Comercialização de Energia Elétrica"
    assert d["VTOT"].tipo == "variavel" and d["VTOT"].definicao == "Valor total do agente no mês"


def test_glossario_nao_inventa_expansao():
    assert glossario.siglas_do_texto("O valor foi apurado no período (XYZ).", "x") == []


# --------------------------------------------------------------------------- exportação
def test_vigencia():
    docs = [{"id": 1, "tipo": "pdc", "documento": "1.6", "vigencia_inicio": "01/01/2025", "vigencia_fim": ""},
            {"id": 2, "tipo": "pdc", "documento": "1.6", "vigencia_inicio": "01/06/2026", "vigencia_fim": ""},
            {"id": 3, "tipo": "pdc", "documento": "1.6", "vigencia_inicio": "01/01/2027", "vigencia_fim": ""}]
    assert exportar.escolher_vigentes(docs, date(2026, 11, 13)) == {2}


# --------------------------------------------------------------------------- avaliação
def test_avaliacao_acha_trecho():
    md = conversores.juntar_paginas([PAG1, PAG2])
    cs = [{"id": str(c.ordem), "texto": f"{c.secao}\n\n{c.texto}"} for c in chunking.chunks(md, 700, 20)]
    ps = [avaliacao.Pergunta("P1", "Qual o prazo para o agente enviar os dados?", "", "", "",
                             ["5º dia útil do mês subsequente"]),
          avaliacao.Pergunta("P2", "Pergunta sem resposta", "", "", "", ["frase que não existe no documento"])]
    r = {x.pergunta.id: x for x in avaliacao.avaliar(ps, cs, top_k=3)}
    assert r["P1"].resultado == "achou" and r["P1"].posicao == 1
    assert r["P2"].resultado == "trecho_ausente"


# --------------------------------------------------------------------------- ponta a ponta
def test_pipeline_completo(perfil, monkeypatch):
    def falso(nome, caminho, info, perfil):
        return conv(nome, [PAG1, PAG2])
    monkeypatch.setattr(conversores, "converter", falso)
    perfil.pasta_entrada.mkdir(parents=True)
    shutil.copy(PDF, perfil.pasta_entrada / "pdc-exemplo.pdf")

    assert pipeline.rodar(perfil, "processar") == 0
    with aberto(perfil.banco) as con:
        doc = dict(con.execute("SELECT * FROM documentos").fetchone())
    assert doc["documento"] == "9.9" and doc["versao"] == "2.0" and doc["status_qualidade"] == "ok"

    # Sem conferir metadados, nada é exportado
    pipeline.rodar(perfil, "exportar")
    assert not (perfil.pasta_saida / "manifesto.csv").exists()

    from docrag.cli import main
    assert main(["--perfil", str(perfil.pasta), "conferir", str(doc["id"])]) == 0
    assert pipeline.rodar(perfil, "exportar") == 0
    sp = perfil.pasta_saida / "sharepoint"
    arquivos = sorted(sp.rglob("*.md"))
    assert (sp / "glossario.md").exists()
    chunk = next(a for a in arquivos if "prazos" in a.name).read_text(encoding="utf-8")
    assert "versão 2.0" in chunk and "vigência a partir de 01/01/2026" in chunk and "seção 2 PREMISSAS > 2.1 Prazos" in chunk
    with open(perfil.pasta_saida / "manifesto.csv", encoding="utf-8-sig") as f:
        linhas = list(csv.DictReader(f))
    assert len(linhas) == len(arquivos) - 1

    # Correção à mão no final.md é preservada ao reverificar
    final = pipeline.pasta_doc(perfil, doc) / "final.md"
    final.write_text(final.read_text(encoding="utf-8").replace("MS+10du", "MS+10du (corrigido)"), encoding="utf-8")
    pipeline.rodar(perfil, "verificar")
    assert "(corrigido)" in final.read_text(encoding="utf-8")


def test_pdf_duplicado_nao_reimporta(perfil, monkeypatch):
    monkeypatch.setattr(conversores, "converter", lambda n, c, i, p: conv(n, [PAG1, PAG2]))
    perfil.pasta_entrada.mkdir(parents=True)
    shutil.copy(PDF, perfil.pasta_entrada / "a.pdf")
    shutil.copy(PDF, perfil.pasta_entrada / "b.pdf")
    pipeline.rodar(perfil, "processar")
    with aberto(perfil.banco) as con:
        assert con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1


def test_portao_pega_numero_colado_em_letras(info, perfil):
    errado = PAG2.replace("MS+10du", "MS+12du")
    rel = qualidade.avaliar(info, conv("docling", [PAG1, errado]), conv("marker", [PAG1, PAG2]), perfil.limiares)
    assert any(v.codigo == "numeros" and v.pagina == 2 for v in rel.problemas())


def test_importa_pdfs_de_subpastas(perfil, monkeypatch):
    monkeypatch.setattr(conversores, "converter", lambda n, c, i, p: conv(n, [PAG1, PAG2]))
    sub = perfil.pasta_entrada / "PdCs" / "Modulo 9"
    sub.mkdir(parents=True)
    shutil.copy(PDF, sub / "9.9.PDF")
    assert pipeline.rodar(perfil, "processar") == 0
    with aberto(perfil.banco) as con:
        assert con.execute("SELECT arquivo FROM documentos").fetchone()[0] == "PdCs/Modulo 9/9.9.PDF"
