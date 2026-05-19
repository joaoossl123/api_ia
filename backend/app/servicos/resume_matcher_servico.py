"""
Motor inspirado em Resume Matcher: combina TF-IDF lexical, similaridade semântica
(Sentence-Transformers) e sobreposição de competências extraídas do CV vs. vaga.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from app.configuracao import Configuracao

_PARARAS = frozenset(
    """
    a o os as um uma de do da em no na por para com sem que se e ou como mais
    ser ter foi esta este essa esse seus suas seu sua
    """.split()
)


@lru_cache(maxsize=2)
def _modelo_embeddings(nome_modelo: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(nome_modelo)


def _tokens(texto: str) -> list[str]:
    t = (texto or "").lower()
    return [w for w in re.findall(r"[a-záàâãéêíóôõúç0-9]{3,}", t) if w not in _PARARAS]


def _similaridade_tfidf(texto_a: str, texto_b: str) -> float:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        ta, tb = set(_tokens(texto_a)), set(_tokens(texto_b))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(1, len(ta | tb))
    vec = TfidfVectorizer(max_features=4000, ngram_range=(1, 2), min_df=1)
    mat = vec.fit_transform([texto_a[:12_000], texto_b[:12_000]])
    sim = float(cosine_similarity(mat[0:1], mat[1:2])[0][0])
    return max(0.0, min(1.0, sim))


def _similaridade_semantica(texto_a: str, texto_b: str, nome_modelo: str) -> float:
    modelo = _modelo_embeddings(nome_modelo)
    emb = modelo.encode(
        [texto_a[:8_000], texto_b[:8_000]],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    sim = float(np.dot(emb[0], emb[1]))
    return max(0.0, min(1.0, (sim + 1.0) / 2.0))


def extrair_competencias_vaga(descricao_vaga: str) -> list[str]:
    """Competências pedidas na vaga (tokens + mapa interno)."""
    from app.servicos.pyresparser_servico import _COMPETENCIAS_BUSCA

    tn = (descricao_vaga or "").lower()
    found: list[str] = []
    for rotulo, variantes in _COMPETENCIAS_BUSCA.items():
        if any(v in tn for v in variantes):
            found.append(rotulo)
    extras = _tokens(descricao_vaga)
    for e in extras:
        if len(e) >= 4 and e not in _PARARAS and e not in found:
            if re.search(r"(python|java|react|sql|excel|scrum|docker|aws)", e):
                found.append(e)
    return list(dict.fromkeys(found))[:30]


def _overlap_skills(habilidades_cv: list[str], competencias_vaga: list[str]) -> float:
    if not competencias_vaga:
        return 0.5
    cv = {h.lower().strip() for h in habilidades_cv if h}
    vg = {c.lower().strip() for c in competencias_vaga if c}
    if not cv:
        return 0.0
    hits = 0
    for v in vg:
        if v in cv:
            hits += 1
            continue
        if any(v in c or c in v for c in cv):
            hits += 1
    return hits / max(1, len(vg))


def _lacunas_competencias(habilidades_cv: list[str], competencias_vaga: list[str]) -> list[str]:
    cv = {h.lower().strip() for h in habilidades_cv if h}
    lacunas: list[str] = []
    for v in competencias_vaga:
        vl = v.lower().strip()
        if vl in cv:
            continue
        if any(vl in c or c in vl for c in cv):
            continue
        lacunas.append(v)
    return lacunas[:8]


def pontuar_candidato_resume_matcher(
    config: "Configuracao",
    descricao_vaga: str,
    texto_cv: str,
    dados_estruturados: dict[str, Any] | None,
    competencias_vaga: list[str] | None = None,
) -> tuple[float, str, list[str]]:
    """
    Pontuação 0–1 e justificativa estilo Resume Matcher (aderência + lacunas).
    """
    comps = competencias_vaga if competencias_vaga is not None else extrair_competencias_vaga(descricao_vaga)
    ds = dados_estruturados or {}
    habs = [str(x) for x in (ds.get("habilidades") or [])]

    w_sem = float(getattr(config, "PESO_RM_SEMANTICO", 0.45))
    w_tfidf = float(getattr(config, "PESO_RM_TFIDF", 0.25))
    w_skill = float(getattr(config, "PESO_RM_SKILLS", 0.20))
    w_exp = float(getattr(config, "PESO_RM_EXPERIENCIA", 0.10))

    sem = _similaridade_semantica(
        descricao_vaga,
        texto_cv,
        getattr(config, "NOME_MODELO_EMBEDDINGS", "paraphrase-multilingual-MiniLM-L12-v2"),
    )
    tfidf = _similaridade_tfidf(descricao_vaga, texto_cv)
    skill = _overlap_skills(habs, comps)
    anos = ds.get("experiencia_anos")
    exp_score = 0.55
    if isinstance(anos, (int, float)) and anos > 0:
        exp_score = min(1.0, float(anos) / 8.0)

    p = w_sem * sem + w_tfidf * tfidf + w_skill * skill + w_exp * exp_score
    if comps and skill < 0.28:
        p *= 0.52
    if tfidf < 0.18:
        p *= 0.58
    if sem > 0.55 and tfidf < 0.22 and skill < 0.35:
        p *= 0.45
    p = float(max(0.0, min(1.0, p)))

    from app.servicos.precisao_analise import calibrar_afinidade

    p = calibrar_afinidade(descricao_vaga, texto_cv, p)

    lacunas = _lacunas_competencias(habs, comps)
    alinhadas = [c for c in comps if c not in lacunas][:6]
    partes: list[str] = []
    if alinhadas:
        partes.append(f"Competências alinhadas: {', '.join(alinhadas)}.")
    if lacunas:
        partes.append(f"Lacunas face à vaga: {', '.join(lacunas)}.")
    partes.append(
        f"Scores Resume Matcher — semântico {sem:.0%}, lexical {tfidf:.0%}, skills {skill:.0%}."
    )
    if isinstance(anos, (int, float)):
        partes.append(f"Experiência estimada no CV: ~{anos:.0f} ano(s).")
    return p, " ".join(partes)[:900], lacunas


def ordenar_por_resume_matcher(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str, dict[str, Any] | None]],
) -> list[tuple[str, float, str | None, int | None, list[str]]]:
    """
    candidatos: (id, texto_cv, dados_estruturados dict ou None)
    Retorna (id, score 0-1, justificativa, score_100, lacunas_competencias)
    """
    comps = extrair_competencias_vaga(descricao_vaga)
    out: list[tuple[str, float, str | None, int | None, list[str]]] = []
    for cid, texto, ds in candidatos:
        p, just, lac = pontuar_candidato_resume_matcher(
            config, descricao_vaga, texto or "", ds, competencias_vaga=comps
        )
        out.append((cid, p, just, int(round(p * 100)), lac))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out
