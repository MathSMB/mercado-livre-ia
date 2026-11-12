# mercado-livre-ia

Base de conhecimento de IA sobre os Procedimentos de Comercialização (PdCs) e as Regras de
Comercialização da CCEE (mercado livre de energia). O desenvolvimento acontece por pull requests.

## Fase 1 · Coletor de PdCs e Regras

Coleta os **Procedimentos de Comercialização (PdCs)** e as **Regras de Comercialização** do site da CCEE,
guarda o histórico num banco local (SQLite) e mostra tudo num painel no navegador. Roda no seu computador,
sem servidor e sem custo.

### Como usar (Windows)

1. Instale o Python 3.11 ou mais novo (https://www.python.org/downloads/, marque "Add Python to PATH").
2. Dê dois cliques em **`iniciar.bat`**. Na primeira vez ele cria o ambiente e instala as dependências
   (alguns minutos). Depois abre o painel no navegador em http://localhost:8501.
3. No painel, clique em **Descobrir + baixar**.

Pelo terminal (opcional):

```bat
.venv\Scripts\activate
python -m ccee.pipeline descobrir        :: lê o site e atualiza o catálogo
python -m ccee.pipeline baixar           :: baixa os pendentes (retoma de onde parou)
python -m ccee.pipeline tudo             :: as duas coisas
python -m ccee.pipeline status           :: resumo
python -m ccee.pipeline baixar --todos   :: inclui histórico (≈1.000 arquivos)
```

### O que ele faz

| Etapa | Como |
| --- | --- |
| Descobrir Regras | Usa a mesma API JSON que a página de Regras usa para listar os cadernos (≈1.066 itens, com histórico). |
| Descobrir PdCs | Lê o HTML da página de PdCs: módulos 1 a 9, provisórios e futuros. |
| Selecionar o corpus | **Regras:** caderno em PDF cujo período de vigência cobre o mês atual (se houver dois, fica o publicado por último). **PdCs:** submódulos das abas de módulo. Provisórios, futuros e histórico ficam catalogados, mas não são baixados por padrão. |
| Baixar | Uma requisição a cada 2 s, 3 tentativas, confere se o arquivo é mesmo PDF/ZIP, grava de forma atômica e calcula SHA-256. |
| Detectar mudanças | Se a URL ou o hash publicado mudarem, o documento volta para "Encontrado" e é baixado de novo. O arquivo antigo continua no disco. |
| Retomar | Cada etapa só processa o que falta. Se cair no meio, rode de novo. Execuções interrompidas ficam marcadas no histórico. |
| Alertas | Inconsistências do próprio site, por exemplo um título que diz "Versão 7.0" com link para o arquivo v6.0. |

### Onde ficam os dados

```
dados/
  pdf/regras/<caderno>/<sha8>_<arquivo>.pdf
  pdf/pdc/<submódulo>/<sha8>_<arquivo>.pdf
  pipeline.db      ← catálogo, execuções e eventos (abra com DB Browser for SQLite, se quiser)
  logs/            ← saída de cada coleta disparada pelo painel
```

Para guardar os dados em outro lugar, defina a variável de ambiente `BASE_CCEE_DADOS`.

### Automatizar (opcional)

No **Agendador de Tarefas** do Windows, crie uma tarefa semanal que execute:

```
<pasta do projeto>\.venv\Scripts\python.exe -m ccee.pipeline tudo
```

com "Iniciar em" apontando para a pasta do projeto. O painel mostra o que mudou desde a última coleta.

### Testes

```bat
.venv\Scripts\python -m pytest -q
```

### Próximas fases

Conversão PDF → Markdown (com fórmulas em LaTeX), portões de qualidade e exportação para SharePoint/Azure.
As etapas *Convertido*, *Validado*, *Revisado* e *Exportado* já aparecem no painel e vão sendo preenchidas.
