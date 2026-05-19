"""
Reclassificação de aderência: compara a descrição da vaga com o texto integral do currículo.
Usa cross-encoder multilíngue (par a par) + cobertura de termos, mais aderente que só vetor global.
"""

from __future__ import annotations

import math
import re
import unicodedata

import numpy as np
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.configuracao import Configuracao

# Trecho usado no cross-encoder (maior contexto reduz falso negativo em CV extenso)
TAMANHO_TRECHO_CURRICULO = 6_000

_PARARAS = frozenset(
    """
    a o os as um uma uns umas de do da dos das em no na nos nas por para com sem
    que se e ou mas como seu sua seus suas ao aos às pela pelo pelas pelos
    ter tem foi ser está estão somos sou fui sido sendo ha há ja já não mais
    muito pouco entre quando onde quem me te seja sejam este esta estes essa esses
    todos toda todo também também só mesmo dos das somos foram terá
    """.split()
)

# Sinónimos para termos de vaga (ex.: "almoxarife" no texto da vaga) → experiência a aceitar no CV
_SINONIMOS_ESTOQUE: frozenset[str] = frozenset(
    """
    estoquista estoque almoxarifado almoxarife almox inventario inventario inventário wms
    picking recebimento receb receb. expedicao expedição armazen armazenagem
    separacao separação carga lote
    """.split()
)
_RELACIONADOS_FUNCAO: dict[str, frozenset[str]] = {
    "estoquista": _SINONIMOS_ESTOQUE,
    "estoque": _SINONIMOS_ESTOQUE,
    "almoxarifado": _SINONIMOS_ESTOQUE,
    "almoxarif": _SINONIMOS_ESTOQUE,
    "almoxarife": _SINONIMOS_ESTOQUE,
}

_COMPETENCIAS_CHAVE: dict[str, frozenset[str]] = {
    "python": frozenset({"python", "py"}),
    "fastapi": frozenset({"fastapi"}),
    "flask": frozenset({"flask"}),
    "django": frozenset({"django"}),
    "linux": frozenset({"linux", "ubuntu", "debian", "bash", "shell"}),
    "sql": frozenset({"sql", "postgres", "postgresql", "mysql", "sqlite"}),
    "docker": frozenset({"docker", "container", "containers"}),
    "kubernetes": frozenset({"kubernetes", "k8s"}),
    "aws": frozenset({"aws", "amazon web services"}),
    "gcp": frozenset({"gcp", "google cloud"}),
    "azure": frozenset({"azure"}),
    "javascript": frozenset({"javascript", "js"}),
    "typescript": frozenset({"typescript", "ts"}),
    "react": frozenset({"react", "reactjs"}),
    "node": frozenset({"node", "nodejs"}),
}


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def enriquecer_descricao_vaga(texto: str) -> str:
    """Consultas muito curtas (ex.: só o cargo) ganham contexto para alinhar melhor à função."""
    t = (texto or "").strip()
    if len(t) < 60:
        return (
            f"Perfil e requisitos da vaga: {t}. "
            "Busca-se experiência e competências práticas compatíveis com essa função, "
            "com histórico profissional e responsabilidades alinhados a esse cargo."
        )
    return t


def _tokens_significativos(texto: str) -> set[str]:
    n = _normalizar(texto)
    partes = re.findall(r"[a-záàâãéêíóôõúç]{4,}", n, re.IGNORECASE)
    return {p for p in partes if p not in _PARARAS}


def _termo_aparece_no_texto(nucleo: str, doc: str) -> bool:
    """Cobre o termo, sinónimos mapeados (ex.: estoquista ↔ estoque) e colisão por subpalavra."""
    n = _normalizar(nucleo)
    d = _normalizar(doc)
    if n in d:
        return True
    cj = _RELACIONADOS_FUNCAO.get(n)
    if cj and any(t in d for t in cj if len(t) >= 3):
        return True
    for chave, syns in _RELACIONADOS_FUNCAO.items():
        if n == chave:
            for s in syns:
                if len(s) >= 3 and s in d:
                    return True
    if len(n) >= 5 and n[:5] in d:
        return True
    return False


def cobertura_termos_vaga_no_texto(vaga: str, texto_curriculo: str) -> float:
    """
    0..1: quão bem os termos de função da vaga têm respaldo no currículo.
    """
    tv = _tokens_significativos(vaga)
    if not tv:
        return 0.5
    doc = (texto_curriculo or "") + " "
    partes: list[float] = []
    for w in tv:
        if _termo_aparece_no_texto(w, doc):
            partes.append(1.0)
        else:
            partes.append(0.0)
    return max(0.0, min(1.0, sum(partes) / len(partes)))


def _competencias_chave_obrigatorias(vaga: str) -> set[str]:
    v = _normalizar(vaga)
    obrig = set()
    for chave, aliases in _COMPETENCIAS_CHAVE.items():
        if any(a in v for a in aliases):
            obrig.add(chave)
    return obrig


def _taxa_cobertura_competencias(vaga: str, texto_curriculo: str) -> float:
    obrig = _competencias_chave_obrigatorias(vaga)
    if not obrig:
        return 1.0
    doc = _normalizar(texto_curriculo or "")
    hits = 0
    for comp in obrig:
        aliases = _COMPETENCIAS_CHAVE.get(comp, frozenset({comp}))
        if any(a in doc for a in aliases):
            hits += 1
    return hits / len(obrig)


@lru_cache(maxsize=1)
def _obter_reclassificador_cruzado(nome_modelo: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        nome_modelo,
        max_length=400,
    )


def _pares_id_texto_seguros(candidatos: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for c in candidatos:
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            out.append((str(c[0]), str(c[1])))
    return out


def pontuacoes_pares_cruzado(
    descricao_vaga: str,
    textos_candidato: list[str],
    nome_modelo: str,
) -> list[float]:
    if not textos_candidato:
        return []
    m = _obter_reclassificador_cruzado(nome_modelo)
    par_query = enriquecer_descricao_vaga(descricao_vaga)
    pares: list[list[str]] = []
    for tx in textos_candidato:
        tre = (tx or "")[:TAMANHO_TRECHO_CURRICULO]
        pares.append([par_query, tre])
    bruto = m.predict(
        pares,
        show_progress_bar=False,
        batch_size=4,
        convert_to_numpy=True,
    )
    arr = np.asarray(bruto, dtype=np.float64).reshape(-1)
    out: list[float] = []
    for s in arr:
        v = float(s.item()) if hasattr(s, "item") else float(s)
        out.append(_logit_para_01(v))
    return out


def _logit_para_01(logit: float) -> float:
    return 1.0 / (1.0 + math.exp(-logit))


def pontuacao_final_aderencia(
    config: "Configuracao",
    descricao_vaga: str,
    texto_curriculo: str,
    ponto_cruz: float,
) -> float:
    cov = cobertura_termos_vaga_no_texto(descricao_vaga, texto_curriculo)
    p = (
        config.PESO_CROSS_ENCODER * ponto_cruz
        + config.PESO_COBERTURA_LEXICAL * cov
    )
    tx = texto_curriculo or ""
    tv = _tokens_significativos(descricao_vaga)
    if 1 <= len(tv) <= 3 and any(_termo_aparece_no_texto(t, tx) for t in tv) and cov >= 0.5:
        p = min(1.0, p * 1.06)
    if len(tv) >= 1 and cov < 0.2:
        p *= 0.5
    if len(tv) >= 1 and cov < 0.1:
        p *= 0.6
    cobertura_comp = _taxa_cobertura_competencias(descricao_vaga, tx)
    if cobertura_comp < 0.34:
        p *= 0.65
    elif cobertura_comp < 0.67:
        p *= 0.82
    p = float(p) ** 0.8
    return max(0.0, min(1.0, p))


def ordenar_por_aderencia(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str]],
) -> list[tuple[str, float]]:
    candidatos = _pares_id_texto_seguros(candidatos)
    if not candidatos:
        return []
    ids = [c[0] for c in candidatos]
    textos = [c[1] for c in candidatos]
    n_modelo = config.NOME_MODELO_RECLASSIFICADOR
    cr = pontuacoes_pares_cruzado(descricao_vaga, textos, n_modelo)
    ordenado: list[tuple[str, float]] = []
    for i, ident in enumerate(ids):
        pcr = cr[i] if i < len(cr) else 0.0
        f = pontuacao_final_aderencia(config, descricao_vaga, textos[i], pcr)
        ordenado.append((ident, f))
    ordenado.sort(key=lambda x: -x[1])
    return ordenado


def ordenar_por_aderencia_lexical(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str]],
) -> list[tuple[str, float]]:
    """
    Fallback leve quando o cross-encoder não consegue carregar (RAM/arquivo/corrupto).
    Mantém uma ordenação útil usando cobertura lexical + competências obrigatórias.
    """
    candidatos = _pares_id_texto_seguros(candidatos)
    if not candidatos:
        return []
    out: list[tuple[str, float]] = []
    for ident, texto in candidatos:
        cov = cobertura_termos_vaga_no_texto(descricao_vaga, texto or "")
        comp = _taxa_cobertura_competencias(descricao_vaga, texto or "")
        p = 0.72 * cov + 0.28 * comp
        if cov < 0.2:
            p *= 0.58
        out.append((ident, max(0.0, min(1.0, float(p)))))
    out.sort(key=lambda x: -x[1])
    return out


def justificativa_resumo_local(
    descricao_vaga: str,
    nome_candidato: str,
    texto_curriculo: str,
    pontuacao_0_1: float,
) -> str:
    """
    Explicação curta, legível, específica desta pessoa, quando a filtragem é feita
    sem Gemini (só reclassificador e cobertura de termos).
    """
    nome = (nome_candidato or "Candidato").strip() or "Candidato"
    nome = nome[:120]
    s100 = max(0, min(100, int(round(float(pontuacao_0_1) * 100))))
    tv = sorted(_tokens_significativos(descricao_vaga))[:6]
    doc = (texto_curriculo or "") + " "
    reforco: list[str] = []
    for t in tv:
        if t and (t in _normalizar(doc) or _termo_aparece_no_texto(t, doc)):
            reforco.append(t)
    reforco = reforco[:3]
    if reforco:
        termos = "”, “".join(reforco)
        return (
            f"Para a vaga pedida, o currículo de {nome} mostra sinais de ligação com a busca (termos da vaga no CV: “{termos}”). "
            f"Aderência automática: {s100}%. Confirme a experiência e o título lendo o PDF."
        )
    if s100 >= 50:
        return (
            f"{nome} ficou com aderência {s100}% (modelo par a par + texto do CV, sem IA generativa). "
            "A ordem resulta de similaridade semântica: valide a função lendo o anexo."
        )
    return (
        f"{nome} tem aderência {s100}% com critério automático. Poucos termos da vaga "
        "aparecem literalmente no trecho indexado, ou a função pode não coincidir com o que procura. "
        "Vale a pena abrir o PDF e confirmar (ou refinar a descrição da vaga)."
    )
