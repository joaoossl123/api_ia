"""
Calibração de scores com base em evidências objetivas no CV (cobertura lexical + competências).
Reduz falsos positivos de similaridade semântica genérica.
"""

from __future__ import annotations

from app.servicos.reclassificacao_vaga import (
    _competencias_chave_obrigatorias,
    _taxa_cobertura_competencias,
    _tokens_significativos,
    _termo_aparece_no_texto,
    cobertura_termos_vaga_no_texto,
)


def evidencias_no_cv(descricao_vaga: str, texto_curriculo: str, max_itens: int = 5) -> list[str]:
    """Termos/competências da vaga com respaldo explícito no texto do CV."""
    doc = (texto_curriculo or "") + " "
    encontrados: list[str] = []
    for t in sorted(_tokens_significativos(descricao_vaga)):
        if t and _termo_aparece_no_texto(t, doc) and t not in encontrados:
            encontrados.append(t)
        if len(encontrados) >= max_itens:
            break
    from app.servicos.reclassificacao_vaga import _COMPETENCIAS_CHAVE, _normalizar

    doc_n = _normalizar(doc)
    for comp in sorted(_competencias_chave_obrigatorias(descricao_vaga)):
        if comp not in encontrados:
            aliases = _COMPETENCIAS_CHAVE.get(comp, frozenset({comp}))
            if any(a in doc_n for a in aliases):
                encontrados.append(comp)
        if len(encontrados) >= max_itens:
            break
    return encontrados[:max_itens]


def calibrar_afinidade(
    descricao_vaga: str,
    texto_curriculo: str,
    pontuacao_bruta_0_1: float,
) -> float:
    """
    Ajusta pontuação 0–1 usando teto derivado de evidências no PDF indexado.
    Perfis sem termos/competências alinhados não mantêm scores altos.
    """
    p = max(0.0, min(1.0, float(pontuacao_bruta_0_1)))
    cov = cobertura_termos_vaga_no_texto(descricao_vaga, texto_curriculo)
    comp = _taxa_cobertura_competencias(descricao_vaga, texto_curriculo)
    tem_comp_obrig = bool(_competencias_chave_obrigatorias(descricao_vaga))

    # Teto teórico: sem evidência lexical/competência, score alto é inconsistente
    teto = 0.18 + 0.42 * cov + (0.28 * comp if tem_comp_obrig else 0.22 * max(cov, comp))
    p = min(p, teto)

    if cov < 0.12:
        p = min(p, 0.15)
    elif cov < 0.28:
        p = min(p, 0.32)
    if tem_comp_obrig and comp < 0.34:
        p = min(p, 0.38)
    if cov < 0.15 and comp < 0.25:
        p = min(p, 0.10)

    # Curva conservadora (evita inflar notas médias)
    p = float(p) ** 1.1
    return max(0.0, min(1.0, p))


def calibrar_score_0_100(
    descricao_vaga: str,
    texto_curriculo: str,
    score_100: int,
) -> tuple[float, int]:
    p = calibrar_afinidade(descricao_vaga, texto_curriculo, score_100 / 100.0)
    return p, max(0, min(100, int(round(p * 100))))
