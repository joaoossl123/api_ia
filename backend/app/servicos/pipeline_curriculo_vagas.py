"""
Pipeline: currículo (texto) → spaCy (extração) → Sentence-Transformers (aderência a vagas)
→ Transformers zero-shot (classificação de área de perfil).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import torch
from sentence_transformers import SentenceTransformer, util

# Vagas padrão: título e texto usado no embedding (PT-BR, áreas típicas de TI/dados)
VAGAS_EXEMPLO: list[tuple[str, str]] = [
    (
        "Desenvolvedor Backend",
        "Desenvolvedor de software com foco em servidores, APIs REST, microsserviços, bancos de "
        "dados relacionais, autenticação, performance, integrações e linguagens como Python, "
        "Java, C#, Go ou Node no backend.",
    ),
    (
        "Desenvolvedor Frontend",
        "Desenvolvedor de interfaces web com HTML, CSS, JavaScript, React, Vue ou Angular, "
        "acessibilidade, consumo de APIs, UX e aplicações SPA responsivas.",
    ),
    (
        "Analista de Dados",
        "Analista de dados com SQL, modelagem, painéis em Power BI ou similares, análise "
        "de métricas, visualização, qualidade e governança básica de dados para o negócio.",
    ),
    (
        "Cientista de Dados",
        "Cientista de dados: machine learning, modelagem estatística, Python, feature "
        "engineering, experimentos e avaliação de modelos, frequentemente com pandas e sklearn.",
    ),
    (
        "Engenheiro de Dados",
        "Engenheiro de dados: pipelines, ETL/ELT, lago/warehouse, cloud, orquestração (Airflow "
        "ou similar), bancos analíticos e data streaming.",
    ),
    (
        "DevOps / SRE",
        "Engenheiro DevOps, SRE ou de plataformas: CI/CD, contêineres, Kubernetes, observabilidade, "
        "IaC (Terraform) e nuvens AWS, Azure ou GCP.",
    ),
    (
        "Analista de Sistemas / Requisitos",
        "Análise e especificação de requisitos, regras de negócio, documentação de sistemas, "
        "ponte entre negócios e desenvolvimento, testes e qualidade de software alinhada ao processo.",
    ),
]

# Zero-shot NLI (distilbart/bart mnlI): rótulo em inglês; exibição em português
# O PT no currículo costuma alinhar bem a esta camada.
PERFIS_NLI_EN_PT: list[tuple[str, str]] = [
    (
        "server-side development, Python, Java, APIs, microservices, SQL databases, backend",
        "Dev backend / serviços & APIs",
    ),
    (
        "frontend, React, JavaScript, HTML, CSS, user interfaces, web applications",
        "Desenv. frontend & web",
    ),
    (
        "data analysis, business intelligence, SQL, dashboards, reporting, metrics, analytics",
        "Analista de Dados (BI, SQL, métricas)",
    ),
    (
        "machine learning, data science, statistics, modeling, experiments, scikit-learn",
        "Cientista de Dados & ML",
    ),
    (
        "data engineering, ETL, pipelines, data lake, data warehouse, Spark, streaming",
        "Eng. de Dados (ETL, lago, pipelines)",
    ),
    (
        "DevOps, CI, CD, cloud, Kubernetes, Docker, infrastructure, site reliability, SRE",
        "DevOps, cloud & SRE",
    ),
    (
        "IT project, product, agile, coordination, people management, product owner, scrum",
        "Produto, projetos & agilidade",
    ),
    (
        "IT security, networking, help desk, end user support, infrastructure",
        "Segurança, redes & suporte a infra",
    ),
]

HIPOTESE_NLI: str = "This text is about {}."


@dataclass
class ResultadoPipelineCurriculo:
    texto_usado: str
    spacy_entidades: list[dict[str, str]]
    spacy_resumo: str
    aderencia_vagas: list[dict[str, Any]] = field(default_factory=list)
    classificacao_perfil: list[dict[str, Any]] = field(default_factory=list)
    detalhe_erro: str | None = None
    # "nli" = Transformers zero-shot; "st_semantica" = ST cos-sim sobre descrições de área
    motor_perfil: str = "nli"


_nlp: Any = None
_st: SentenceTransformer | None = None
_st_ultimo_nome: str = ""
_zs: Any = None
_zs_ultimo_nome: str = ""


def _obter_nlp() -> Any:
    global _nlp
    if _nlp is not None:
        return _nlp
    import spacy  # type: ignore

    try:
        _nlp = spacy.load("pt_core_news_sm")
    except OSError as e:
        raise RuntimeError(
            "Modelo spaCy em português não instalado. Execute, na pasta do backend, após o pip: "
            "python -m spacy download pt_core_news_sm"
        ) from e
    return _nlp


def _obter_st(nome_modelo: str) -> SentenceTransformer:
    global _st, _st_ultimo_nome
    if _st is not None and _st_ultimo_nome == nome_modelo:
        return _st
    _st = SentenceTransformer(nome_modelo)
    _st_ultimo_nome = nome_modelo
    return _st


def _obter_zero_shot(nome_modelo: str) -> Any:
    global _zs, _zs_ultimo_nome
    if _zs is not None and _zs_ultimo_nome == nome_modelo:
        return _zs
    from transformers import pipeline

    dispositivo = 0 if torch.cuda.is_available() else -1
    # torch_dtype evita avisos em modelos com pesos em half
    _zs = pipeline(
        "zero-shot-classification",
        model=nome_modelo,
        device=dispositivo,
    )
    _zs_ultimo_nome = nome_modelo
    return _zs


def _extrair_spacy(texto: str, limite_caracteres: int) -> tuple[list[dict[str, str]], str, str]:
    nlp = _obter_nlp()
    trecho = (texto or "").strip()[: max(1, limite_caracteres)]
    doc = nlp(trecho)
    vistos: set[tuple[str, str]] = set()
    entidades: list[dict[str, str]] = []
    for e in doc.ents:
        ch = (e.text.strip(), e.label_)
        if len(e.text.strip()) < 2 or ch in vistos:
            continue
        vistos.add(ch)
        entidades.append({"texto": e.text.strip(), "rotulo": e.label_})

    partes: list[str] = []
    if len(trecho) < 6_000:
        for p in doc.noun_chunks:
            t = p.text.strip()
            if 2 < len(t) < 50:
                partes.append(t)
    resumo_ent = ", ".join(f"{a['texto']} ({a['rotulo']})" for a in entidades[:40])
    enriquecido = trecho
    if resumo_ent:
        enriquecido = f"{trecho}\n[spaCy: entidades principais: {resumo_ent}]"
    if partes and len(" ".join(partes)) < 400:
        enriquecido += f"\n[conceitos: {', '.join(partes[:25])}]"

    return entidades, resumo_ent, enriquecido


def _pencentual_similaridade(emb_cv: torch.Tensor, emb_v: torch.Tensor) -> float:
    c = float(util.pytorch_cos_sim(emb_cv, emb_v).item())
    if c < 0.0:
        c = 0.5 * (c + 1.0)
    return max(0.0, min(1.0, c))


def _aderencia_vagas(
    texto_rico: str,
    nome_st: str,
    vagas: list[tuple[str, str]],
    tamanho_max: int,
) -> list[dict[str, Any]]:
    if not (texto_rico or "").strip() or not vagas:
        return []
    m = _obter_st(nome_st)
    base = texto_rico[:tamanho_max]
    embc = m.encode([base], convert_to_tensor=True, show_progress_bar=False)
    out: list[dict[str, Any]] = []
    for titulo, desc in vagas:
        docu = f"{titulo} — {desc}"
        embv = m.encode([docu], convert_to_tensor=True, show_progress_bar=False)
        sim = _pencentual_similaridade(embc, embv)
        pct = int(round(sim * 100))
        out.append({"vaga_titulo": titulo, "vaga_texto": desc, "pontuacao_0_1": round(sim, 4), "percentual": pct})
    out.sort(key=lambda x: x["percentual"], reverse=True)
    return out


def _classificar_perfil_por_nli(
    texto: str,
    nome_modelo: str,
    tamanho_max: int,
    pares: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    t = (texto or "").strip()[:tamanho_max] or "."
    orig = pares or PERFIS_NLI_EN_PT
    en = [a[0] for a in orig]
    if not en:
        return []
    z = _obter_zero_shot(nome_modelo)
    res = z(
        t,
        en,
        multi_label=True,
        hypothesis_template=HIPOTESE_NLI,
    )
    by_en: dict[str, str] = {a[0]: a[1] for a in orig}
    labels_raw = res.get("labels", []) or []
    scores_raw = res.get("scores", []) or []
    labels_ = labels_raw.tolist() if hasattr(labels_raw, "tolist") else list(labels_raw)
    scores_ = scores_raw.tolist() if hasattr(scores_raw, "tolist") else list(scores_raw)
    saida: list[dict[str, Any]] = []
    for i, le in enumerate(labels_):
        sc = float(scores_[i]) if i < len(scores_) else 0.0
        exibir = by_en.get(le, le)[:200]
        saida.append(
            {
                "rotulo": exibir,
                "pontuacao_0_1": round(sc, 4),
                "percentual": int(round(sc * 100)),
            }
        )
    return sorted(saida, key=lambda x: x["percentual"], reverse=True)


def _classificar_perfil_por_st(
    texto: str, nome_st: str, tamanho_max: int, pares: list[tuple[str, str]] | None = None
) -> list[dict[str, Any]]:
    """Fallback: BERT/encoder (via multilingue) — mesmo motor que a aderência às vagas."""
    t = (texto or "").strip()[:tamanho_max] or "."
    orig = pares or PERFIS_NLI_EN_PT
    m = _obter_st(nome_st)
    emb_t = m.encode([t], convert_to_tensor=True, show_progress_bar=False)
    saida: list[dict[str, Any]] = []
    for en, pt in orig:
        docu = f"Perfil: {en}. Contexto: {pt}."
        embp = m.encode([docu], convert_to_tensor=True, show_progress_bar=False)
        sim = _pencentual_similaridade(emb_t, embp)
        saida.append(
            {
                "rotulo": str(pt)[:200],
                "pontuacao_0_1": round(sim, 4),
                "percentual": int(round(sim * 100)),
            }
        )
    return sorted(saida, key=lambda x: x["percentual"], reverse=True)


def _parse_vagas_de_entrada(bruto: str | None) -> list[tuple[str, str]] | None:
    if not bruto or not str(bruto).strip():
        return None
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise ValueError(f"vagas (JSON) inválido: {e}") from e
    if not isinstance(dados, list):
        raise ValueError("vagas deve ser uma lista de objetos com 'nome' e 'descricao'.")
    pares: list[tuple[str, str]] = []
    for item in dados:
        if not isinstance(item, dict):
            continue
        n = (item.get("nome") or item.get("titulo") or "").strip()
        d = (item.get("descricao") or item.get("texto") or "").strip()
        if n and d:
            pares.append((n, d))
    if not pares:
        return None
    return pares


def executar_pipeline_curriculo(
    texto_bruto: str,
    config: Any,
    json_vagas_opcional: str | None = None,
    vagas_pares: list[tuple[str, str]] | None = None,
) -> ResultadoPipelineCurriculo:
    texto = (texto_bruto or "").strip()
    if len(texto) < 20:
        return ResultadoPipelineCurriculo(
            texto_usado=texto,
            spacy_entidades=[],
            spacy_resumo="",
            detalhe_erro="Texto do currículo muito curto; envie pelo menos ~20 caracteres de conteúdo real.",
        )

    lim = int(getattr(config, "PIPELINE_TAMANHO_SPA", 20_000))
    ent, res_ent, rico = _extrair_spacy(texto, lim)
    t_vetor = int(getattr(config, "PIPELINE_TAMANHO_VETOR_ST", 10_000))
    t_zs = int(getattr(config, "PIPELINE_TAMANHO_ZERO_SHOT", 3_000))

    try:
        if vagas_pares and len(vagas_pares) > 0:
            vagas = vagas_pares
        else:
            vagas = _parse_vagas_de_entrada(json_vagas_opcional) or VAGAS_EXEMPLO
    except ValueError as e:
        return ResultadoPipelineCurriculo(
            texto_usado=texto[:500],
            spacy_entidades=ent,
            spacy_resumo=res_ent,
            detalhe_erro=str(e),
        )
    st_nome = str(getattr(config, "NOME_MODELO_EMBEDDINGS", "paraphrase-multilingual-MiniLM-L12-v2"))
    zs_nome = str(getattr(config, "NOME_MODELO_ZERO_SHOT", "valhalla/distilbart-mnli-12-3"))
    ader: list[dict[str, Any]] = []
    cls: list[dict[str, Any]] = []
    m_perfil: str = "nli"
    try:
        ader = _aderencia_vagas(rico, st_nome, vagas, t_vetor)
    except Exception as e:  # pragma: no cover
        return ResultadoPipelineCurriculo(
            texto_usado=texto[:500],
            spacy_entidades=ent,
            spacy_resumo=res_ent,
            detalhe_erro=f"Similaridade (Sentence-Transformers): {e!s}",
        )
    try:
        cls = _classificar_perfil_por_nli(
            texto,
            zs_nome,
            t_zs,
        )
    except Exception as e_nli:  # pragma: no cover
        try:
            cls = _classificar_perfil_por_st(texto, st_nome, t_vetor, None)
            m_perfil = "st_semantica"
        except Exception as e_st:  # pragma: no cover
            return ResultadoPipelineCurriculo(
                texto_usado=texto[:800] if len(texto) > 800 else texto,
                spacy_entidades=ent,
                spacy_resumo=res_ent,
                aderencia_vagas=ader,
                classificacao_perfil=[],
                detalhe_erro=f"Classificação de perfil: NLI ({e_nli!s}) e fallback ST ({e_st!s}).",
            )

    return ResultadoPipelineCurriculo(
        texto_usado=f"{texto[:800]}…" if len(texto) > 800 else texto,
        spacy_entidades=ent,
        spacy_resumo=res_ent,
        aderencia_vagas=ader,
        classificacao_perfil=cls,
        detalhe_erro=None,
        motor_perfil=m_perfil,
    )
