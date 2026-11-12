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

# Fontes oficiais
URL_REGRAS = "https://www.ccee.org.br/mercado/regras-de-comercializacao"
URL_REGRAS_API = (
    "https://www.ccee.org.br/web/guest/mercado/regras-de-comercializacao"
    "?p_p_id=org_br_ccee_regras_comercializacao_portlet_CCEERegrasPortlet_INSTANCE_pybk"
    "&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view&p_p_cacheability=cacheLevelPage"
)
PREFIXO_REGRAS = "_org_br_ccee_regras_comercializacao_portlet_CCEERegrasPortlet_INSTANCE_pybk_"
URL_PDCS = "https://www.ccee.org.br/mercado/procedimentos-de-comercializacao"

# Boas maneiras com o site
INTERVALO_SEGUNDOS = 2.0      # pausa mínima entre requisições
TENTATIVAS = 3                # novas tentativas em caso de falha
TIMEOUT_SEGUNDOS = 60
ITENS_POR_PAGINA = 100
MAX_PAGINAS = 50              # trava de segurança da paginação
USER_AGENT = "base-ccee-coletor/0.1 (uso interno; coleta de documentos publicos)"
