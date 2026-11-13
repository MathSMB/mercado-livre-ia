"""Configurações do coletor. Ajuste aqui, sem precisar mexer no resto do código."""
from pathlib import Path
import os

# Pasta raiz do projeto (onde fica este repositório)
RAIZ = Path(__file__).resolve().parent.parent

# Pasta de dados: pode ser trocada pela variável de ambiente BASE_CCEE_DADOS
DADOS = Path(os.environ.get("BASE_CCEE_DADOS", RAIZ / "dados"))
PASTA_PDF = DADOS / "pdf"            # arquivos originais baixados
PASTA_ENTRADA = DADOS / "entrada"    # PDFs baixados à mão, para o comando "importar"
PASTA_MARKDOWN = DADOS / "markdown"  # (próxima fase) conversões
PASTA_TRECHOS = DADOS / "trechos"    # (próxima fase) chunks + metadados
PASTA_EXPORTAR = DADOS / "exportar"  # (próxima fase) pacote para SharePoint/Azure
BANCO = DADOS / "pipeline.db"        # histórico e status (SQLite, arquivo único)
TRAVA = DADOS / ".execucao.lock"     # impede duas execuções ao mesmo tempo

# Páginas oficiais onde baixar os documentos (o site bloqueia acesso automatizado)
URL_REGRAS = "https://www.ccee.org.br/mercado/regras-de-comercializacao"
URL_PDCS = "https://www.ccee.org.br/mercado/procedimentos-de-comercializacao"

