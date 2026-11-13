"""Perfil de projeto: tudo o que é específico de um corpus (CCEE ou outro) fica aqui.

Um perfil é uma pasta com `perfil.toml` e, opcionalmente, `documentos.csv` (metadados
confirmados à mão), `glossario.csv` e `perguntas.csv`. O motor do docrag não conhece a CCEE;
ele só lê o perfil.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TipoDocumento:
    """Como reconhecer um tipo de documento e tirar seus metadados do texto."""
    nome: str
    rotulo: str
    detectar: list[str]                     # regex; basta uma casar no início do texto
    # campo -> regex com 1 grupo, ou tabela {padrao, ocorrencia = "primeira"|"ultima", depois_de, antes_de}
    campos: dict[str, str | dict] = field(default_factory=dict)
    paginas_metadados: int = 3              # quantas páginas iniciais olhar
    conversor_principal: str | None = None  # sobrepõe o padrão do perfil
    # campo -> regra(s) de uma segunda fonte no texto que precisa dar o MESMO valor (conferência cruzada)
    conferir: dict[str, str | dict] = field(default_factory=dict)
    # campo -> regex com 1 grupo aplicada ao nome do arquivo (ex. versão "v7.0" no nome)
    arquivo: dict[str, str] = field(default_factory=dict)

    def casa(self, texto: str) -> bool:
        return any(re.search(p, texto) for p in self.detectar)

    def extrair(self, texto: str, regras: dict | None = None) -> dict[str, str]:
        achados = {}
        for campo, regra in (self.campos if regras is None else regras).items():
            if isinstance(regra, str):
                regra = {"padrao": regra}
            trecho = texto
            if regra.get("depois_de"):
                m = re.search(regra["depois_de"], trecho)
                trecho = trecho[m.end():] if m else ""
            if regra.get("antes_de"):
                m = re.search(regra["antes_de"], trecho)
                trecho = trecho[:m.start()] if m else trecho
            ms = list(re.finditer(regra["padrao"], trecho))
            if ms:
                if regra.get("ocorrencia") == "maior":  # a data mais recente (dd.mm.aaaa ou dd/mm/aaaa)
                    m = max(ms, key=lambda x: tuple(reversed(re.split(r"[./\-]", x.group(1)))))
                else:
                    m = ms[-1] if regra.get("ocorrencia") == "ultima" else ms[0]
                valor = " ".join(m.group(1).split())
                if campo.startswith("vigencia"):  # 01.07.2025 -> 01/07/2025
                    valor = re.sub(r"^(\d{2})[.\-](\d{2})[.\-](\d{4})$", r"\1/\2/\3", valor)
                achados[campo] = valor
        return achados


@dataclass
class Limiares:
    cobertura_aviso: float = 0.98      # fração das palavras do PDF presentes na conversão
    cobertura_falha: float = 0.90
    concordancia_aviso: float = 0.90   # semelhança entre as duas conversões, por página
    concordancia_falha: float = 0.70
    min_palavras_pagina: int = 15      # abaixo disso a página é tratada como "quase vazia"


@dataclass
class Chunking:
    max_palavras: int = 700
    min_palavras: int = 60
    nivel_maximo: int = 3              # seções mais profundas que isso ficam dentro da mãe


@dataclass
class Automacao:
    """O que o programa decide sozinho (ver decisão D14: conferência por exceção)."""
    confirmar_metadados: bool = True   # metadados confirmados por conferência cruzada não pedem clique
    auditoria_1_em: int = 50           # 1 em cada N documentos aprovados sozinhos vai para revisão humana (0 = nunca)
    busca_minima: float = 0.8          # teste automático de busca: fração mínima de acertos no top 3


@dataclass
class Perfil:
    pasta: Path
    nome: str
    descricao: str
    conversor_principal: str
    conversores: list[str]
    campos_obrigatorios: list[str]
    tipos: list[TipoDocumento]
    limiares: Limiares
    chunking: Chunking
    pasta_entrada: Path
    pasta_trabalho: Path
    pasta_saida: Path
    docling: dict = field(default_factory=dict)
    marker: dict = field(default_factory=dict)
    arbitro: bool = True                 # troca página ruim do principal pela leitura que bate com o PDF
    automacao: "Automacao" = None

    def __post_init__(self):
        if self.automacao is None:
            self.automacao = Automacao()

    @property
    def banco(self) -> Path:
        return self.pasta_trabalho / "docrag.db"

    def arquivo(self, nome: str) -> Path:
        return self.pasta / nome

    def tipo_de(self, texto_inicio: str) -> TipoDocumento | None:
        for t in self.tipos:
            if t.casa(texto_inicio):
                return t
        return None

    def tipo(self, nome: str | None) -> TipoDocumento | None:
        return next((t for t in self.tipos if t.nome == nome), None)

    def principal_para(self, tipo: str | None) -> str:
        t = self.tipo(tipo)
        return (t.conversor_principal if t and t.conversor_principal else self.conversor_principal)


def carregar(pasta: str | Path) -> Perfil:
    pasta = Path(pasta).resolve()
    with open(pasta / "perfil.toml", "rb") as f:
        d = tomllib.load(f)

    def caminho(chave: str, padrao: str) -> Path:
        p = Path(d.get("pastas", {}).get(chave, padrao))
        return p if p.is_absolute() else (pasta / p).resolve()

    tipos = [TipoDocumento(nome=t["nome"], rotulo=t.get("rotulo", t["nome"]), detectar=t.get("detectar", []),
                           campos=t.get("campos", {}), conversor_principal=t.get("conversor_principal"),
                           paginas_metadados=t.get("paginas_metadados", 3),
                           conferir=t.get("conferir", {}), arquivo=t.get("arquivo", {}))
             for t in d.get("tipos", [])]
    conv = d.get("conversao", {})
    return Perfil(
        pasta=pasta,
        nome=d.get("nome", pasta.name),
        descricao=d.get("descricao", ""),
        conversor_principal=conv.get("principal", "docling"),
        conversores=conv.get("conversores", ["docling", "marker"]),
        campos_obrigatorios=d.get("metadados", {}).get("obrigatorios", ["documento", "titulo", "versao", "vigencia_inicio"]),
        tipos=tipos,
        limiares=Limiares(**d.get("limiares", {})),
        chunking=Chunking(**d.get("chunking", {})),
        pasta_entrada=caminho("entrada", "dados/entrada"),
        pasta_trabalho=caminho("trabalho", "dados/trabalho"),
        pasta_saida=caminho("saida", "dados/saida"),
        docling=d.get("docling", {}),
        marker=d.get("marker", {}),
        arbitro=conv.get("arbitro", True),
        automacao=Automacao(**d.get("automacao", {})),
    )
