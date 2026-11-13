# mercado-livre-ia

Base de conhecimento de IA sobre os Procedimentos de Comercialização (PdCs) e as Regras de
Comercialização da CCEE (mercado livre de energia). O desenvolvimento acontece por pull requests.

## Fase 1 · Catálogo de PdCs e Regras

Guarda os **Procedimentos de Comercialização (PdCs)** e as **Regras de Comercialização** da CCEE num banco local
(SQLite), com versão, hash e histórico, e mostra tudo num painel no navegador. Roda no seu computador, sem
servidor e sem custo.

O site da CCEE bloqueia acesso automatizado (HTTP 403 em todo o domínio), então **os arquivos são baixados
pelo navegador** e o painel faz o resto. Ver D10 no registro de decisões.

### Como usar (Windows)

1. Instale o Python 3.11 ou mais novo (https://www.python.org/downloads/, marque "Add Python to PATH").
2. Baixe pelo navegador, **sem renomear**:
   - **Regras:** https://www.ccee.org.br/mercado/regras-de-comercializacao → selecione os cadernos vigentes →
     botão **Download das Regras** (vem um ZIP).
   - **PdCs:** https://www.ccee.org.br/mercado/procedimentos-de-comercializacao → em cada aba de módulo,
     **Acesse o PdC**. A versão mostrada nas abas de módulo é a vigente; *Futuros* e *Provisórios* ficam à parte.
3. Coloque os arquivos em `dados\entrada\`.
4. Rode `.\iniciar.bat` (na primeira vez ele cria o ambiente e instala as dependências) e clique em
   **📥 Importar PDFs da pasta**.

Pelo terminal (opcional):

```bat
.venv\Scripts\activate
python -m ccee.pipeline importar                  :: cataloga o que está em dados\entrada
python -m ccee.pipeline importar --pasta C:\x    :: ou de outra pasta
python -m ccee.pipeline status                    :: resumo
```

### O que a importação faz

| Etapa | Como |
| --- | --- |
| Identificar | Lê caderno/submódulo e versão pelo nome do arquivo, como o site publica (`05 - Contratos_2026.6.4_JUN.pdf`, `1.6_-_Comercializacao_varejista_v6.0.pdf`). Nome não reconhecido vira alerta. |
| Conferir | Recusa arquivo que não é PDF/ZIP de verdade (ex.: página de erro salva com extensão .pdf). |
| ZIP | Abre o ZIP do botão *Download das Regras* e importa cada PDF de dentro. |
| Guardar | Copia para `dados/pdf/<fonte>/<código>/<sha8>_<arquivo>` e calcula SHA-256. |
| Versões | Reimportar o mesmo arquivo não duplica. Versão nova substitui no catálogo e a antiga continua no disco. |
| Histórico | Cada importação fica registrada (execuções e eventos), com os erros visíveis no painel. |

### Onde ficam os dados

```
dados/
  pdf/regras/<caderno>/<sha8>_<arquivo>.pdf
  pdf/pdc/<submódulo>/<sha8>_<arquivo>.pdf
  pipeline.db      ← catálogo, execuções e eventos (abra com DB Browser for SQLite, se quiser)
  entrada/         ← onde você coloca os PDFs/ZIPs baixados
  logs/            ← saída de cada importação disparada pelo painel
```

Para guardar os dados em outro lugar, defina a variável de ambiente `BASE_CCEE_DADOS`.

### Testes

```bat
.venv\Scripts\python -m pytest -q
```

### Próximas fases

Conversão PDF → Markdown (com fórmulas em LaTeX), portões de qualidade e exportação para SharePoint/Azure.
As etapas *Convertido*, *Validado*, *Revisado* e *Exportado* já aparecem no painel e vão sendo preenchidas.
