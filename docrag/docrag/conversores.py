"""Leitores do PDF: Docling, Marker e a camada de texto do próprio PDF.

Cada conversor devolve o Markdown separado por página, para que o portão de qualidade
compare página com página. Docling e Marker só são importados quando usados, porque
carregam modelos grandes.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path


@dataclass
class Conversao:
    conversor: str
    versao: str
    paginas: list[str]                 # Markdown de cada página; índice 0 = página 1
    segundos: float = 0.0
    avisos: list[str] = field(default_factory=list)

    @property
    def markdown(self) -> str:
        return juntar_paginas(self.paginas)


_CACHE: dict = {}
MARCA_PAGINA = "<!-- página {n} -->"
RE_MARCA_PAGINA = re.compile(r"^<!-- página (\d+) -->$", re.M)


def juntar_paginas(paginas: list[str]) -> str:
    partes = []
    for i, p in enumerate(paginas, start=1):
        partes.append(MARCA_PAGINA.format(n=i))
        partes.append(p.strip())
    return "\n\n".join(partes).strip() + "\n"


def separar_paginas(markdown: str) -> list[str]:
    """Inverso de juntar_paginas. Texto antes da primeira marca vai para a página 1."""
    marcas = list(RE_MARCA_PAGINA.finditer(markdown))
    if not marcas:
        return [markdown.strip()]
    n = max(int(m.group(1)) for m in marcas)
    paginas = [""] * n
    paginas[0] = markdown[: marcas[0].start()].strip()
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(markdown)
        idx = int(m.group(1)) - 1
        paginas[idx] = (paginas[idx] + "\n\n" + markdown[m.end():fim].strip()).strip()
    return paginas


def _versao(pacote: str) -> str:
    try:
        return metadata.version(pacote)
    except metadata.PackageNotFoundError:
        return "?"


# --------------------------------------------------------------------------- PDF
@dataclass
class InfoPdf:
    n_paginas: int
    textos: list[str]                  # camada de texto de cada página (vazio = página escaneada/imagem)
    imagens: list[int]                 # nº de imagens em cada página


def ler_pdf(caminho: Path) -> InfoPdf:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pdf = pdfium.PdfDocument(str(caminho))
    try:
        textos, imagens = [], []
        for pagina in pdf:
            tp = pagina.get_textpage()
            textos.append(tp.get_text_range())
            imagens.append(sum(1 for _ in pagina.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=2)))
            tp.close()
            pagina.close()
        return InfoPdf(n_paginas=len(textos), textos=textos, imagens=imagens)
    finally:
        pdf.close()


# --------------------------------------------------------------------------- Docling
def converter_docling(caminho: Path, opcoes: dict | None = None) -> Conversao:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opcoes = opcoes or {}
    po = PdfPipelineOptions()
    po.do_ocr = bool(opcoes.get("ocr", False))
    po.do_table_structure = True
    po.table_structure_options.mode = TableFormerMode.ACCURATE
    po.table_structure_options.do_cell_matching = True
    po.do_formula_enrichment = bool(opcoes.get("formulas", True))
    chave = ("docling", po.do_ocr, po.do_formula_enrichment)
    if chave not in _CACHE:  # carregar os modelos demora; reaproveita entre documentos
        _CACHE[chave] = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=po)})
    conv = _CACHE[chave]

    t0 = time.perf_counter()
    res = conv.convert(str(caminho))
    doc = res.document
    n = doc.num_pages()
    paginas = [doc.export_to_markdown(page_no=i) for i in range(1, n + 1)]
    avisos = [str(e) for e in (getattr(res, "errors", None) or [])]
    return Conversao("docling", _versao("docling"), paginas, time.perf_counter() - t0, avisos)


# --------------------------------------------------------------------------- Marker
RE_SEPARADOR_MARKER = re.compile(r"\n*\{(\d+)\}-{48}\n*")


def separar_paginas_marker(texto: str, n_paginas: int) -> list[str]:
    """Com paginate_output o Marker escreve `{N}` + 48 hífens antes de cada página (N começa em 0)."""
    paginas = [""] * n_paginas
    partes = RE_SEPARADOR_MARKER.split(texto)
    # partes = [antes, n0, texto0, n1, texto1, ...]
    if partes[0].strip() and n_paginas:
        paginas[0] = partes[0].strip()
    for i in range(1, len(partes) - 1, 2):
        idx = int(partes[i])
        if 0 <= idx < n_paginas:
            paginas[idx] = (paginas[idx] + "\n\n" + partes[i + 1].strip()).strip()
    return paginas


def converter_marker(caminho: Path, n_paginas: int, opcoes: dict | None = None) -> Conversao:
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    opcoes = opcoes or {}
    config = {"output_format": "markdown", "paginate_output": True, "disable_image_extraction": True,
              "use_llm": False, **opcoes.get("config", {})}
    t0 = time.perf_counter()
    if "marker_modelos" not in _CACHE:
        _CACHE["marker_modelos"] = create_model_dict()
    conv = PdfConverter(artifact_dict=_CACHE["marker_modelos"], config=config)
    texto, _, _ = text_from_rendered(conv(str(caminho)))
    paginas = separar_paginas_marker(texto, n_paginas)
    return Conversao("marker", _versao("marker-pdf"), paginas, time.perf_counter() - t0)


def converter(nome: str, caminho: Path, info: InfoPdf, perfil) -> Conversao:
    if nome == "docling":
        return converter_docling(caminho, perfil.docling)
    if nome == "marker":
        return converter_marker(caminho, info.n_paginas, perfil.marker)
    raise ValueError(f"Conversor desconhecido: {nome}")
