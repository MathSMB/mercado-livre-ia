# docrag

Transforma PDFs em uma base pronta para RAG (SharePoint + Copilot Studio), com portão de qualidade.
É um programa independente: não usa nada do coletor/scraper e pode ser levado para outros projetos.
O que é específico de um corpus (aqui, CCEE) fica num **perfil** (`perfis/ccee/`).

```
PDF baixado à mão ──► Docling (texto principal) ─┐
                 ├──► Marker  (2ª leitura)  ─────┼──► portão de qualidade ──► chunks por seção ──► pasta p/ SharePoint
                 └──► camada de texto do PDF ────┘        (+ aceite humano)       + metadados        + glossário
                                                                                                    + perguntas de teste
```

Tudo roda no seu computador, sem API paga. Docling e Marker baixam seus modelos (centenas de MB) na
primeira execução e depois funcionam offline. Não geramos vetores: o Copilot/SharePoint indexa sozinho.

## Como usar (Windows)

1. Dê dois cliques em `iniciar.bat`. Na primeira vez ele cria o ambiente e instala tudo (demora).
2. Coloque os PDFs baixados do site da CCEE em `dados/entrada/` (sem renomear). Pode ser em subpastas; o mesmo PDF repetido em duas pastas só entra uma vez.
3. No painel, clique em **Processar PDFs novos**.
4. O programa confere sozinho o que der (veja "Conferência por exceção" abaixo). Só sobra para você:
   - aba **Documentos**: metadados que a conferência cruzada não confirmou (a coluna Conferido fica desmarcada);
   - aba **Qualidade**: documentos com aviso, falha ou sorteados para auditoria. Compare o texto do PDF, o texto
     final e a segunda leitura; corrija `final.md` à mão e clique em **Reverificar**, ou registre o aceite
     dizendo o que conferiu.
6. **Exportar para o SharePoint** e suba a pasta `dados/saida/sharepoint/` numa biblioteca do SharePoint.
7. Aba **Perguntas de teste**: escreva perguntas com a resposta certa e rode o teste.

Pela linha de comando: `python -m docrag processar | verificar | exportar | avaliar | conferir ID | aceitar ID --nota "..."`.

## O portão de qualidade

| Verificação | O que compara | Nível |
| --- | --- | --- |
| páginas | nº de páginas do PDF × de cada conversão | falha |
| cobertura | palavras escritas no PDF que estão no texto final (por página; ignora cabeçalho/rodapé repetido) | aviso < 98%, falha < 90% |
| números | todo número da página do PDF (prazos, %, valores, "MS+5du") precisa estar no texto final, e vice-versa | falha (aviso se faltam até 2) |
| concordância | semelhança entre Docling e Marker na página | aviso < 90%, falha < 70% |
| tabelas | quantidade de tabelas nas duas leituras | aviso |
| fórmulas | quantidade e escrita das fórmulas LaTeX nas duas leituras | aviso |
| títulos | seções numeradas encontradas nas duas leituras e saltos na numeração (3.1 → 3.3) | aviso |
| figuras | páginas com imagem (fluxogramas não viram texto) | aviso |
| busca | teste automático: frases do documento (com o prazo apagado) precisam achar o próprio trecho no top 3 | aviso < 80% |

Um documento só é exportado com portão **OK** ou com **aceite registrado**, e com os metadados conferidos.
Se o resultado do portão mudar depois do aceite, o aceite é descartado. Os limites ficam no `perfil.toml`.

### Conferência por exceção (sem IA, só regras)

- **Árbitro página a página:** quando a página do conversor principal não bate com o PDF (palavras ou números)
  e a do outro conversor bate, o texto final usa a do outro. Também troca quando o principal achatou uma tabela
  que o outro manteve. Fica registrado como `info` ("usei a leitura do marker nesta página").
- **Nível `info`:** diferença que o PDF já confirma (os conversores discordam, mas o texto final bate com o PDF;
  fluxograma cujos prazos estão todos na descrição de atividades). Fica no relatório, não pede nada a ninguém.
- **Metadados confirmados sozinhos:** a versão do cabeçalho precisa ser igual à da última linha da tabela de
  revisões, a vigência precisa ser a data mais recente da tabela e o nome do arquivo (ex. `_v7.0.pdf`) não pode
  contradizer. Se algo divergir, o motivo aparece e uma pessoa confere.
- **Auditoria por amostragem:** 1 em cada 50 documentos aprovados sozinhos é sorteado (sempre o mesmo, pelo
  sha256) para uma pessoa conferir. Mede se o portão continua confiável conforme o volume cresce.
- Tudo isso se liga e desliga no `perfil.toml` (`[conversao] arbitro`, `[automacao]`).

## Os chunks

- Um chunk por seção numerada (até o nível 3, ex. `3.2.1`); subseções mais fundas ficam dentro da mãe.
- Nunca corta tabela, fórmula ou parágrafo. Seção maior que 700 palavras é dividida entre parágrafos; tabela
  grande é dividida por linhas repetindo o cabeçalho. Seção curta (ou só título) vai junto com a seguinte do mesmo ramo.
- Cada arquivo `.md` é legível e começa com os metadados e uma linha de contexto, por exemplo:
  `Procedimento de Comercialização 1.6 – … · versão … · vigência a partir de … · seção 3 Atividades > 3.2 Prazos · páginas 5–6`.
- Só a versão **vigente hoje** de cada documento vai para `sharepoint/`; as outras vão para `outras-versoes/`,
  para o Copilot não misturar regras antigas e novas.

## Saída

```
dados/saida/
  sharepoint/                 suba esta pasta
    pdc/1-6_v…/0001-….md
    regra/05_v…/0001-….md
    glossario.md
  outras-versoes/
  manifesto.csv               uma linha por chunk (serve para criar colunas no SharePoint)
  glossario.csv
  relatorios/qualidade-….md
```

## Perfil (para usar em outro projeto)

Copie `perfis/ccee/` e ajuste:

- `perfil.toml`: pastas, conversor principal, limites do portão, tamanho dos chunks e as regras (regex) que
  reconhecem cada tipo de documento e tiram os metadados da capa;
- `documentos.csv`: metadados conferidos (gravado pelo painel; versionado no Git);
- `glossario.csv`: siglas e variáveis escritas à mão (vencem as automáticas);
- `perguntas.csv`: `id, pergunta, resposta_esperada, documento, secao_esperada, trechos`. Em `trechos` cole frases
  exatas do documento que precisam estar no chunk da resposta (várias separadas por `||`);
- `resultados_copilot.csv`: criado pelo teste; registre ali a resposta do Copilot para medir o acerto.

Rode com `python -m docrag --perfil caminho/do/perfil processar` ou `set DOCRAG_PERFIL=...` antes do painel.

## Licenças

Docling: MIT. Marker: código GPL-3 e pesos dos modelos gratuitos só para uso pessoal, pesquisa e empresas
pequenas. Para uso na empresa, use só o Docling (`conversores = ["docling"]`) ou obtenha a licença do Marker.

## Testes

`python -m pytest` (não baixa modelos: os conversores são simulados; `tests/dados/pdc-exemplo.pdf` é um
documento fictício).
