"""Leitura de PDF: pdfplumber (prioritário) + PyMuPDF como reforço, como no fluxo de triagem."""

import re
from pathlib import Path

import fitz  # PyMuPDF

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None  # type: ignore


def _limpar_texto_local(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def extrair_texto_de_pdf(caminho: Path, tamanho_maximo: int) -> str:
    if not caminho.exists() or not caminho.is_file():
        return ""

    texto = ""
    if pdfplumber is not None:
        try:
            with pdfplumber.open(caminho) as pdf:
                for pagina in pdf.pages:
                    extra = pagina.extract_text()
                    if extra:
                        texto += extra + " "
            texto = _limpar_texto_local(texto)
        except Exception:
            texto = ""

    if not texto or len(texto) < 20:
        partes: list[str] = []
        try:
            with fitz.open(caminho) as doc:
                for pagina in doc:
                    partes.append(pagina.get_text("text") or "")
        except Exception:
            return "[erro-nao-foi-possivel-ler-pdf]"
        texto = "\n".join(partes)
        texto = re.sub(r"\r\n", "\n", texto)
        texto = re.sub(r"[\t ]+\n", "\n", texto)
        texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
        texto = _limpar_texto_local(texto)

    if len(texto) > tamanho_maximo:
        texto = texto[:tamanho_maximo] + "\n[...trecho-limitado-para-embeddings...]"

    return texto if texto else "[vazio-ou-apenas-imagens]"
