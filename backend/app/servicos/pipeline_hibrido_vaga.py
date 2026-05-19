"""
Pipeline alternativo: PyResparser (estrutura) + Resume Matcher (ranking) + Gemini (refino).

Ordem:
1. Dados estruturados do CV (pyresparser ou heurística PT) já guardados no upload.
2. Ranking híbrido lexical + semântico + skills (estilo Resume Matcher).
3. Opcional: Gemini re-pontua os top-N para justificativa mais rica (se cota disponível).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.servicos.gemini_talentos import chave_disponivel, ordenar_talentos_por_gemini
from app.servicos.pyresparser_servico import dados_estruturados_de_json
from app.servicos.resume_matcher_servico import ordenar_por_resume_matcher

if TYPE_CHECKING:
    from app.configuracao import Configuracao


def _mesclar_com_gemini(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos_trios: list[tuple[str, str, str]],
    ordem_rm: list[tuple[str, float, str | None, int | None, list[str]]],
    max_gemini: int,
) -> tuple[list[tuple[str, float, str | None, int | None]], bool]:
    """Substitui scores dos top-N pelo Gemini quando a API responder."""
    base = [(a, b, c, d) for a, b, c, d, _ in ordem_rm]
    if not candidatos_trios or not ordem_rm:
        return base, False
    top_ids = [t[0] for t in ordem_rm[:max_gemini]]
    por_id = {c[0]: c for c in candidatos_trios}
    pool = [por_id[i] for i in top_ids if i in por_id]
    if not pool:
        return base, False
    try:
        ordem_g = ordenar_talentos_por_gemini(config, descricao_vaga, pool)
    except Exception:
        return base, False
    mapa_g = {t[0]: t for t in ordem_g}
    out: list[tuple[str, float, str | None, int | None]] = []
    vistos: set[str] = set()
    for cid, _, _, _ in ordem_rm:
        if cid in mapa_g:
            g = mapa_g[cid]
            just = (g[2] or "").strip()
            rm = next((x for x in ordem_rm if x[0] == cid), None)
            if rm and rm[4]:
                just = f"{just} Lacunas (Resume Matcher): {', '.join(rm[4][:5])}.".strip()
            out.append((g[0], float(g[1]), just[:1500] if just else rm[2] if rm else None, g[3]))
            vistos.add(cid)
        else:
            rm = next(x for x in ordem_rm if x[0] == cid)
            out.append((rm[0], rm[1], rm[2], rm[3]))
            vistos.add(cid)
    out.sort(key=lambda x: (-float(x[1] or 0.0), str(x[0])))
    return out, True


def executar_pipeline_hibrido_vaga(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str, str]],
    dados_json_por_id: dict[str, str | None],
) -> tuple[
    list[tuple[str, float, str | None, int | None]],
    str,
    dict[str, list[str]],
    bool,
]:
    """
    candidatos: (id, nome, texto_indexado)
    dados_json_por_id: id -> JSON estruturado do upload
  Returns: (ordem, mensagem_componentes)
    """
    comps_rm: list[tuple[str, str, dict | None]] = []
    for cid, _nome, texto in candidatos:
        ds = dados_estruturados_de_json(dados_json_por_id.get(cid))
        comps_rm.append((cid, texto, ds))

    ordem_rm = ordenar_por_resume_matcher(config, descricao_vaga, comps_rm)
    msg = (
        "Motor híbrido: PyResparser/heurística PT (skills no upload) + "
        "Resume Matcher (TF-IDF + semântico + competências)"
    )

    usar_gemini = (
        chave_disponivel(config)
        and not bool(getattr(config, "PREFERIR_MOTOR_LOCAL", False))
        and bool(getattr(config, "HIBRIDO_USAR_GEMINI", True))
    )
    ordem = [(a, b, c, d) for a, b, c, d, _ in ordem_rm]
    usou_gemini = False
    if usar_gemini:
        max_g = max(4, int(getattr(config, "GEMINI_MAX_CANDIDATOS_LOTE", 18)))
        ordem, usou_gemini = _mesclar_com_gemini(config, descricao_vaga, candidatos, ordem_rm, max_g)
        if usou_gemini:
            msg += f" + Gemini ({getattr(config, 'NOME_MODELO_GEMINI', 'gemini')}) nos top {max_g}."
        else:
            msg += " (Gemini indisponível; scores do Resume Matcher mantidos.)"
    elif chave_disponivel(config) and bool(getattr(config, "PREFERIR_MOTOR_LOCAL", False)):
        msg += " (Gemini omitido: PREFERIR_MOTOR_LOCAL=true)."
    elif not chave_disponivel(config):
        msg += " (sem chave Gemini; só Resume Matcher)."

    lacunas_map = {t[0]: list(t[4]) for t in ordem_rm}
    return ordem, msg, lacunas_map, usou_gemini
