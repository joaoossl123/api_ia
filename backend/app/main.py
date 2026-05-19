from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import uuid
import unicodedata
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session

from app.banco_dados import RegistroCurriculo, abrir_sessao, init_banco
from app.configuracao import Configuracao, obter_caminho_backend, resolver_caminho
from app.modelos import (
    AnaliseCompatibilidadeVagasResposta,
    AnaliseVagaResposta,
    AderenciaVagaItem,
    CandidatoResultadoResposta,
    CurriculoResumo,
    EnvioVagaRequisicao,
    MensagemSimples,
    PerfilClassificadoItem,
    RequisicaoCompatibilidadeVagas,
    SpacyEntidadeResumo,
)
from app.servicos.ia_banco_talentos import BancoVetorialTalentos
from app.servicos.pdf_servico import extrair_texto_de_pdf
from app.servicos.gemini_talentos import (
    _trios_candidatos_seguros,
    chave_disponivel,
    ordenar_talentos_por_gemini,
)
from app.servicos.reclassificacao_vaga import (
    justificativa_resumo_local,
    ordenar_por_aderencia,
    ordenar_por_aderencia_lexical,
)
from app.servicos.pipeline_curriculo_vagas import ResultadoPipelineCurriculo, executar_pipeline_curriculo
from app.servicos.pipeline_hibrido_vaga import executar_pipeline_hibrido_vaga
from app.servicos.pyresparser_servico import (
    extrair_dados_estruturados,
    serializar_dados_estruturados,
)

app = FastAPI(
    title="Análise de Currículos (IA + Banco de talentos)",
    version="1.0.0",
    default_response_class=JSONResponse,
)


@app.exception_handler(Exception)
async def tratamento_erro_generico(request: Request, exc: Exception) -> JSONResponse:
    """Preserva 422/4xx do FastAPI; regista traceback e devolve 500 com mensagem opcional."""
    if isinstance(exc, RequestValidationError):
        return await request_validation_exception_handler(request, exc)
    if isinstance(exc, StarletteHTTPException):
        return await http_exception_handler(request, exc)
    logging.getLogger("uvicorn.error").exception(
        "Erro não tratado em %s %s",
        request.method,
        request.url.path,
    )
    # Por defeito mostra a causa (útil em dev). Em produção: OCULTAR_ERROS_500=1 no .env
    if os.getenv("OCULTAR_ERROS_500", "").lower() in ("1", "true", "yes"):
        detalhe = "Erro interno do servidor."
    else:
        detalhe = f"{type(exc).__name__}: {exc}"
    return JSONResponse(status_code=500, content={"detail": detalhe})


config = Configuracao()
banco_vetor: BancoVetorialTalentos | None = None


def obter_banco_vetorial() -> BancoVetorialTalentos:
    """Inicialização preguiçosa: o primeiro acesso carrega o modelo (pode demorar na 1.ª execução)."""
    global banco_vetor
    if banco_vetor is None:
        banco_vetor = BancoVetorialTalentos(config)
    return banco_vetor


@app.get("/saude", tags=["sistema"], summary="Verificação viva do serviço")
def verificar_saude() -> dict:
    return {"situacao": "operante", "nome_sistema": config.NOME_SISTEMA}


origens = [o.strip() for o in config.ORIGEM_PERMITIDA_FRONTEND.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origens or ["*"],
    # Qualquer porta em localhost/127.0.0.1 (Vite escolhe 5173, 5174, … automaticamente)
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api", tags=["talentos"])


def get_db() -> Session:
    s = abrir_sessao()
    try:
        yield s
    finally:
        s.close()


def _resposta_analise_compat(
    r: ResultadoPipelineCurriculo,
) -> AnaliseCompatibilidadeVagasResposta:
    ent = [SpacyEntidadeResumo(texto=e["texto"], rotulo=e["rotulo"]) for e in r.spacy_entidades]
    ad = [
        AderenciaVagaItem(
            vaga_titulo=a["vaga_titulo"],
            percentual=a["percentual"],
            pontuacao_0_1=a["pontuacao_0_1"],
        )
        for a in r.aderencia_vagas
    ]
    pf = [
        PerfilClassificadoItem(
            rotulo=p["rotulo"],
            percentual=p["percentual"],
            pontuacao_0_1=p["pontuacao_0_1"],
        )
        for p in r.classificacao_perfil
    ]
    if r.detalhe_erro and not (ad or pf):
        msg = f"Falha no pipeline: {r.detalhe_erro}"
    elif r.detalhe_erro:
        msg = f"Concluído com avisos: {r.detalhe_erro}"
    else:
        base = "Concluído: spaCy (entidades) + Sentence-Transformers (vagas) + classificação de área (perfil)"
        if getattr(r, "motor_perfil", "nli") == "st_semantica":
            msg = f"{base}; perfil via similaridade de embeddings (NLI indisponível na máquina ou offline)."
        else:
            msg = f"{base} (Transformers NLI zero-shot)."
    return AnaliseCompatibilidadeVagasResposta(
        spacy_resumo=r.spacy_resumo,
        spacy_entidades=ent,
        aderencia_vagas=ad,
        classificacao_perfil=pf,
        trecho_texto=r.texto_usado,
        detalhe_erro=r.detalhe_erro,
        mensagem=msg,
        motor_perfil=getattr(r, "motor_perfil", "nli") or "nli",
    )


@router.post(
    "/curriculo/compatibilidade",
    response_model=AnaliseCompatibilidadeVagasResposta,
    summary="spaCy, Sentence-Transformers e Transformers: aderência a vagas e rótulo de área (zero-shot)",
)
def analisar_compatibilidade_vagas(
    requisicao: RequisicaoCompatibilidadeVagas,
) -> AnaliseCompatibilidadeVagasResposta:
    pares: list[tuple[str, str]] | None = None
    if requisicao.vagas and len(requisicao.vagas) > 0:
        pares = []
        for v in requisicao.vagas:
            t = (v.nome or "").strip()
            d = (v.descricao or "").strip() or t
            if t:
                pares.append((t, d))
    r = executar_pipeline_curriculo(requisicao.texto, config, vagas_pares=pares)
    if r.detalhe_erro and (r.detalhe_erro.startswith("Texto do currículo") or "muito curto" in (r.detalhe_erro or "")):
        raise HTTPException(400, detail=r.detalhe_erro)
    return _resposta_analise_compat(r)


@router.post(
    "/curriculo/compatibilidade-pdf",
    response_model=AnaliseCompatibilidadeVagasResposta,
    summary="Análise de compatibilidade (mesmo motor) a partir de ficheiro PDF",
)
def analisar_compatibilidade_vagas_pdf(
    arquivo: UploadFile = File(...),
    vagas_json: str | None = Form(
        default=None,
        description='Opcional. JSON: [{"nome":"Título", "descricao":"texto da vaga para embedding"}]',
    ),
) -> AnaliseCompatibilidadeVagasResposta:
    _ = _apenas_nome_extensao(arquivo.filename or "curriculo.pdf")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        alvo = Path(f.name)
    try:
        with alvo.open("wb") as stream:
            shutil.copyfileobj(arquivo.file, stream)
        texto = extrair_texto_de_pdf(alvo, config.TAMANHO_TRECHO_PDF)
    finally:
        alvo.unlink(missing_ok=True)
    if not texto or texto.startswith("["):
        raise HTTPException(400, detail="Não foi possível extrair texto útil do PDF. Verifique o ficheiro.")
    r = executar_pipeline_curriculo(texto, config, json_vagas_opcional=vagas_json)
    if r.detalhe_erro and (r.detalhe_erro.startswith("Texto do currículo") or "muito curto" in (r.detalhe_erro or "")):
        raise HTTPException(400, detail=r.detalhe_erro)
    return _resposta_analise_compat(r)


@app.on_event("startup")
def on_startup() -> None:
    init_banco(config)
    pasta = resolver_caminho(config, config.PASTA_CURRICULOS)
    pasta.mkdir(parents=True, exist_ok=True)


@router.get("/sistema/informacoes", response_model=dict)
def informacoes() -> dict:
    gem = chave_disponivel(config)
    return {
        "nome_sistema": config.NOME_SISTEMA,
        "descricao": config.MENSAGEM_BOAS_VINDAS,
        "corte_pontuacao": config.CORTE_PONTUACAO_MINIMA,
        "modelo": config.NOME_MODELO_EMBEDDINGS,
        "modelo_reclassificador": config.NOME_MODELO_RECLASSIFICADOR,
        "modelo_analise_vaga": config.NOME_MODELO_GEMINI,
        "modelo_perfil_zeroshot": config.NOME_MODELO_ZERO_SHOT,
        "spacy_modelo": "pt_core_news_sm (instale com: python -m spacy download pt_core_news_sm)",
        "pipeline_compatibilidade": "spaCy → entidades; Sentence-Transformers → aderência a vagas; "
        "Transformers (zero-shot) → classificação de área de perfil",
        "motor_classificacao": (
            "local"
            if bool(getattr(config, "PREFERIR_MOTOR_LOCAL", False))
            else ("gemini" if gem else "local")
        ),
        "chave_gemini_configurada": bool(gem),
        "preferir_motor_local": bool(getattr(config, "PREFERIR_MOTOR_LOCAL", False)),
        "gemini_max_candidatos_lote": int(getattr(config, "GEMINI_MAX_CANDIDATOS_LOTE", 18)),
        "motor_analise_vaga_padrao": getattr(config, "MOTOR_ANALISE_VAGA", "padrao"),
        "usar_pyresparser": bool(getattr(config, "USAR_PYRESPARSER", True)),
        "hibrido_usar_gemini": bool(getattr(config, "HIBRIDO_USAR_GEMINI", True)),
    }


@router.get("/curriculos", response_model=list[CurriculoResumo])
def listar(db: Session = Depends(get_db)) -> list[CurriculoResumo]:
    rows = db.query(RegistroCurriculo).order_by(RegistroCurriculo.criado_em.desc()).all()
    out: list[CurriculoResumo] = []
    for r in rows:
        out.append(
            CurriculoResumo(
                id=r.id,
                nome_candidato=r.nome_candidato,
                email=r.email,
                nome_arquivo=r.nome_arquivo_original,
                criado_em=r.criado_em,
                trecho_vista_previa=r.resumo_vista_previa,
            )
        )
    return out


def _apenas_nome_extensao(nome: str) -> str:
    nome = Path(nome).name
    if not nome.lower().endswith(".pdf"):
        raise HTTPException(400, detail="Apenas arquivos em formato PDF.")
    if re.search(r"[\\/]", nome) or ".." in nome:
        raise HTTPException(400, detail="Nome de arquivo inválido.")
    return nome


def _mensagem_fallback_gemini(err: BaseException) -> str:
    txt = str(err).lower()
    if "gemini_quota" in txt or "resource_exhausted" in txt or "429" in txt:
        return (
            "Cota da API Gemini esgotada (HTTP 429). A análise seguiu com o reclassificador local "
            "(sem custo Google). Para voltar ao Gemini: aguarde o reset da cota (free tier ~1 min), "
            "ative faturação em https://aistudio.google.com/apikey , ou defina PREFERIR_MOTOR_LOCAL=true "
            "no .env para não tentar a API."
        )
    return "Gemini indisponível; a análise seguiu com o reclassificador local."


def _mensagem_modo_local(chave_configurada: bool) -> str:
    if chave_configurada:
        return (
            "Análise com reclassificador local. A chave Gemini está configurada, mas o serviço "
            "está indisponível neste momento."
        )
    return (
        "Análise com reclassificador local. Defina CHAVE_API_GEMINI (ou GOOGLE_API_KEY) no "
        f".env do backend para usar o modelo {config.NOME_MODELO_GEMINI}."
    )


def _score_local_0_100(pontuacao_0_1: float) -> int:
    p = max(0.0, min(1.0, float(pontuacao_0_1)))
    return int(round(p * 100))


def _ordenar_candidatos_local(
    desc: str,
    cands_local: list[tuple[str, str]],
) -> list[tuple[str, float]]:
    try:
        return ordenar_por_aderencia(config, desc, cands_local)
    except Exception:
        return ordenar_por_aderencia_lexical(config, desc, cands_local)


def _pool_candidatos_para_gemini(
    desc: str,
    candidatos: list[tuple[str, str, str]],
    cands_local: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Limita CVs enviados ao Gemini para não estourar cota/tokens."""
    max_g = max(4, int(getattr(config, "GEMINI_MAX_CANDIDATOS_LOTE", 18)))
    if len(candidatos) <= max_g:
        return candidatos
    ord_loc = _ordenar_candidatos_local(desc, cands_local)
    top_ids = [a for a, _ in ord_loc[:max_g]]
    por_id = {c[0]: c for c in candidatos}
    return [por_id[cid] for cid in top_ids if cid in por_id]


def _mesclar_ordem_gemini_com_local(
    ordem_gem: list[tuple[str, float, str | None, int | None]],
    ord_loc: list[tuple[str, float]],
) -> list[tuple[str, float, str | None, int | None]]:
    """Mantém scores Gemini nos pré-selecionados; o restante usa reclassificação local."""
    vistos = {t[0] for t in ordem_gem}
    resto = [(a, b, None, None) for a, b in ord_loc if a not in vistos]
    mesclada = list(ordem_gem) + resto
    mesclada.sort(key=lambda x: (-float(x[1] or 0.0), str(x[0])))
    return mesclada


def _normalizar_texto_busca(texto: str) -> str:
    n = unicodedata.normalize("NFKD", texto or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return n.lower()


def _tokens_requisito(requisito: str) -> list[str]:
    base = _normalizar_texto_busca(requisito)
    return re.findall(r"[a-z0-9]{3,}", base)


def _requisito_tem_evidencia_no_cv(requisito: str, texto_cv: str) -> bool:
    req = _normalizar_texto_busca(requisito).strip()
    doc = _normalizar_texto_busca(texto_cv)
    if not req:
        return True
    if req in doc:
        return True
    toks = _tokens_requisito(requisito)
    if not toks:
        return False
    hits = sum(1 for t in toks if t in doc)
    min_hits = max(1, int(round(len(toks) * 0.75)))
    return hits >= min_hits


def _avaliar_requisitos_obrigatorios(
    texto_cv: str,
    requisitos_obrigatorios: list[str],
) -> tuple[bool, list[str]]:
    reqs = [r.strip() for r in requisitos_obrigatorios if (r or "").strip()]
    if not reqs:
        return True, []
    lacunas: list[str] = []
    for req in reqs:
        if not _requisito_tem_evidencia_no_cv(req, texto_cv):
            lacunas.append(req)
    return len(lacunas) == 0, lacunas


def _descricao_vaga_com_requisitos(
    descricao: str,
    obrigatorios: list[str],
    desejaveis: list[str],
) -> str:
    ob = [r.strip() for r in obrigatorios if (r or "").strip()]
    de = [r.strip() for r in desejaveis if (r or "").strip()]
    partes = [descricao.strip()]
    if ob:
        partes.append("Requisitos obrigatórios:\n- " + "\n- ".join(ob))
    if de:
        partes.append("Requisitos desejáveis:\n- " + "\n- ".join(de))
    return "\n\n".join([p for p in partes if p]).strip()


@router.post(
    "/curriculos/enviar",
    response_model=CurriculoResumo,
    summary="Enviar currículo em PDF para o banco de talentos (teste inicial)",
)
def enviar_curriculo(
    candidato: str = Form(..., description="Nome do candidato"),
    email: str | None = Form(None),
    arquivo: UploadFile = File(..., description="Arquivo PDF do currículo"),
    db: Session = Depends(get_db),
) -> CurriculoResumo:
    bv = obter_banco_vetorial()
    nome = (candidato or "").strip()[:200]
    if len(nome) < 2:
        raise HTTPException(400, detail="Informe o nome do candidato (mín. 2 caracteres).")
    n_arq = _apenas_nome_extensao(arquivo.filename or "curriculo.pdf")

    ident = str(uuid.uuid4())
    base = obter_caminho_backend()
    sub = Path(config.PASTA_CURRICULOS)
    alvo = base / sub / f"{ident}__{n_arq}"
    with alvo.open("wb") as f:
        shutil.copyfileobj(arquivo.file, f)

    texto = extrair_texto_de_pdf(alvo, config.TAMANHO_TRECHO_PDF)
    if not texto or texto.startswith("["):
        alvo.unlink(missing_ok=True)
        raise HTTPException(400, detail="Não foi possível extrair texto útil do PDF. Verifique o arquivo.")

    rel = str((sub / f"{ident}__{n_arq}").as_posix())
    prev = (texto[: 1800] + "…") if len(texto) > 1800 else texto
    email_l = (email or "").strip()[:200] or None

    dados_cv = extrair_dados_estruturados(config, alvo, texto)
    dados_json = serializar_dados_estruturados(dados_cv)

    bv.inserir_curriculo(
        texto,
        {
            "candidato_id": ident,
            "nome_candidato": nome,
            "email": email_l or "",
            "nome_arquivo": n_arq,
        },
    )
    r = RegistroCurriculo(
        id=ident,
        nome_candidato=nome,
        email=email_l,
        nome_arquivo_original=n_arq,
        caminho_relativo_pdf=rel,
        texto_indexado=texto,
        resumo_vista_previa=prev,
        dados_estruturados_json=dados_json,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return CurriculoResumo(
        id=r.id,
        nome_candidato=r.nome_candidato,
        email=r.email,
        nome_arquivo=r.nome_arquivo_original,
        criado_em=r.criado_em,
        trecho_vista_previa=r.resumo_vista_previa,
    )


@router.delete(
    "/curriculos/{id_candidato}",
    response_model=MensagemSimples,
    summary="Remover currículo do banco, índice e ficheiro",
)
def excluir(id_candidato: str, db: Session = Depends(get_db)) -> MensagemSimples:
    r = db.get(RegistroCurriculo, id_candidato)
    if r is None:
        raise HTTPException(404, detail="Registro inexistente.")
    obter_banco_vetorial().remover(r.id)
    base = obter_caminho_backend()
    cam = base / r.caminho_relativo_pdf
    if cam.is_file():
        cam.unlink()
    db.delete(r)
    db.commit()
    return MensagemSimples(mensagem="Registro excluído com sucesso.")


def _justificativa_para_exibicao(
    db: Session,
    desc_vaga: str,
    cid: str,
    p: float,
    just_modelo: str | None,
    via_gemini: bool,
    score_0_100: int | None,
) -> str | None:
    """Garante texto útil: IA com tom humano, ou explicação local com nome e factos do CV."""
    r = db.get(RegistroCurriculo, cid)
    if r is None:
        return (just_modelo or "").strip() or None
    t = (just_modelo or "").strip()
    if t:
        return t[:1500]
    if not via_gemini:
        return justificativa_resumo_local(
            desc_vaga,
            str(r.nome_candidato or "—"),
            r.texto_indexado or "",
            p,
        )
    n = (r.nome_candidato or "Candidato")[:100]
    sc = score_0_100 if score_0_100 is not None else int(round(p * 100))
    return (
        f"{n} — {sc}/100 na análise por IA. O modelo não devolveu detalhe extra; abra o PDF e "
        "confronte com a vaga."
    )


def _montar_resposta_candidato(
    db: Session,
    cid: str,
    p: float,
    justificativa: str | None = None,
    score_0_100: int | None = None,
    atende_requisitos_obrigatorios: bool | None = None,
    lacunas_requisitos_obrigatorios: list[str] | None = None,
    lacunas_competencias_vaga: list[str] | None = None,
) -> CandidatoResultadoResposta | None:
    r = db.get(RegistroCurriculo, cid)
    if r is None:
        return None
    return CandidatoResultadoResposta(
        id_candidato=cid,
        nome_candidato=str(r.nome_candidato)[:200],
        email=r.email,
        nome_arquivo_original=str(r.nome_arquivo_original)[:500],
        pontuacao_afinidade=round(p, 4),
        justificativa=justificativa,
        score_0_100=score_0_100,
        atende_requisitos_obrigatorios=atende_requisitos_obrigatorios,
        lacunas_requisitos_obrigatorios=lacunas_requisitos_obrigatorios or [],
        lacunas_competencias_vaga=lacunas_competencias_vaga or [],
    )


@router.post(
    "/vaga/analise-hibrida",
    response_model=AnaliseVagaResposta,
    summary="Análise híbrida: PyResparser + Resume Matcher + Gemini (opcional)",
)
def analisar_vaga_hibrida(
    requisicao: EnvioVagaRequisicao,
    db: Session = Depends(get_db),
) -> AnaliseVagaResposta:
    requisicao = requisicao.model_copy(update={"motor_analise": "hibrido"})
    return analisar_vaga(requisicao, db)


@router.post(
    "/vaga/analise",
    response_model=AnaliseVagaResposta,
    summary="Pesquisar no banco os currículos mais alinhados à vaga (IA)",
)
def analisar_vaga(
    requisicao: EnvioVagaRequisicao,
    db: Session = Depends(get_db),
) -> AnaliseVagaResposta:
    """Usa a descrição da vaga e reclassifica os CVs (par a par) em função do texto do PDF indexado."""
    n = requisicao.quantidade_sugerida
    corte = config.CORTE_PONTUACAO_MINIMA
    requisitos_obrigatorios = [r for r in (requisicao.requisitos_obrigatorios or []) if (r or "").strip()]
    requisitos_desejaveis = [r for r in (requisicao.requisitos_desejaveis or []) if (r or "").strip()]
    desc = _descricao_vaga_com_requisitos(
        requisicao.descricao_da_vaga,
        requisitos_obrigatorios,
        requisitos_desejaveis,
    )
    total_reg = int(db.query(RegistroCurriculo).count() or 0)
    if total_reg == 0:
        return AnaliseVagaResposta(
            mensagem_status="Nenhum currículo no banco. Envie ficheiros PDF e tente novamente.",
            total_antes_corte=0,
            resultados=[],
        )

    chave_configurada = chave_disponivel(config)
    max_local = max(20, int(getattr(config, "MAX_CANDIDATOS_RECLASSIFICACAO_LOCAL", 120)))
    pool_min = max(8, int(getattr(config, "POOL_BUSCA_VETORIAL_MIN", 24)))
    pool_mul = max(2, int(getattr(config, "POOL_BUSCA_VETORIAL_MULTIPLICADOR", 6)))
    pool_suplementar = max(16, int(getattr(config, "POOL_SUPLEMENTAR_MAXIMO", 120)))

    # (id, nome, texto indexado) — Gemini usa nome+trecho; fallback local usa só pares
    candidatos: list[tuple[str, str, str]] = []
    if total_reg <= config.MAX_CURRICULOS_TODOS_NA_ANALISE:
        for r in db.query(RegistroCurriculo).all():
            if r.texto_indexado and not r.texto_indexado.startswith("["):
                candidatos.append(
                    (r.id, (r.nome_candidato or "—")[:200], r.texto_indexado)
                )
    else:
        try:
            bv = obter_banco_vetorial()
            pool = min(max_local, max(pool_min, n * pool_mul))
            itens = bv.buscar_afinidade(desc, limite=pool)
            for item in itens:
                cid = str(item.get("candidato_id", ""))
                r = db.get(RegistroCurriculo, cid)
                if r and r.texto_indexado and not r.texto_indexado.startswith("["):
                    candidatos.append(
                        (r.id, (r.nome_candidato or "—")[:200], r.texto_indexado)
                    )
            vistos = {a[0] for a in candidatos}
            if len(candidatos) < 8:
                alvo = min(max_local, max(16, n * 2))
                falta = max(0, alvo - len(candidatos))
                limite_extra = min(pool_suplementar, falta)
                extras = (
                    db.query(RegistroCurriculo)
                    .order_by(RegistroCurriculo.criado_em.desc())
                    .limit(limite_extra)
                    .all()
                )
                for r in extras:
                    if r.id in vistos:
                        continue
                    if r.texto_indexado and not r.texto_indexado.startswith("["):
                        candidatos.append(
                            (r.id, (r.nome_candidato or "—")[:200], r.texto_indexado)
                        )
                        vistos.add(r.id)
                    if len(candidatos) >= alvo:
                        break
        except Exception:
            for r in (
                db.query(RegistroCurriculo)
                .order_by(RegistroCurriculo.criado_em.desc())
                .limit(max(max_local, n * pool_mul))
                .all()
            ):
                if r.texto_indexado and not r.texto_indexado.startswith("["):
                    candidatos.append(
                        (r.id, (r.nome_candidato or "—")[:200], r.texto_indexado)
                    )

    candidatos = _trios_candidatos_seguros(candidatos)

    if not candidatos:
        return AnaliseVagaResposta(
            mensagem_status="Não há texto indexado nos ficheiros. Reindexe reenviando os PDFs.",
            total_antes_corte=0,
            resultados=[],
        )

    cands_local: list[tuple[str, str]] = [(a, c) for a, _b, c in candidatos]
    msg_extra = ""
    via_gemini = False
    lacunas_comp_por_id: dict[str, list[str]] = {}
    motor = (requisicao.motor_analise or getattr(config, "MOTOR_ANALISE_VAGA", "padrao") or "padrao").strip().lower()
    if motor not in ("padrao", "hibrido"):
        motor = "padrao"

    if bool(getattr(config, "APENAS_GEMINI", False)) and not chave_configurada:
        raise HTTPException(
            status_code=503,
            detail="Defina CHAVE_API_GEMINI ou GOOGLE_API_KEY no ficheiro .env (pasta backend).",
        )

    if motor == "hibrido":
        dados_map: dict[str, str | None] = {}
        for cid, _, _ in candidatos:
            r = db.get(RegistroCurriculo, cid)
            dados_map[cid] = r.dados_estruturados_json if r else None
        ordem, msg_hibrido, lacunas_comp_por_id, via_gemini = executar_pipeline_hibrido_vaga(
            config, desc, candidatos, dados_map
        )
        total_antes = len(ordem)
        msg_extra = msg_hibrido
    else:
        ord_loc_completa = _ordenar_candidatos_local(desc, cands_local)
        usar_gemini = chave_configurada and not bool(getattr(config, "PREFERIR_MOTOR_LOCAL", False))

        if usar_gemini:
            pool_gemini = _pool_candidatos_para_gemini(desc, candidatos, cands_local)
            try:
                ordem_gem = ordenar_talentos_por_gemini(config, desc, pool_gemini)
                if len(candidatos) > len(pool_gemini):
                    ordem = _mesclar_ordem_gemini_com_local(ordem_gem, ord_loc_completa)
                else:
                    ordem = ordem_gem
                total_antes = len(ordem)
                via_gemini = True
            except Exception as err:
                if bool(getattr(config, "APENAS_GEMINI", False)):
                    raise HTTPException(status_code=502, detail=_mensagem_fallback_gemini(err)) from err
                total_antes = len(ord_loc_completa)
                ordem = [(a, b, None, None) for a, b in ord_loc_completa]
                msg_extra = " " + _mensagem_fallback_gemini(err)
        else:
            total_antes = len(ord_loc_completa)
            ordem = [(a, b, None, None) for a, b in ord_loc_completa]
            if chave_configurada and bool(getattr(config, "PREFERIR_MOTOR_LOCAL", False)):
                msg_extra = " Modo local ativo (PREFERIR_MOTOR_LOCAL=true); Gemini não foi chamado."

    bons: list[CandidatoResultadoResposta] = []
    aproximados: list[CandidatoResultadoResposta] = []
    for tu in ordem:
        cid = tu[0]
        p = float(tu[1])
        just = tu[2] if len(tu) > 2 else None
        s100 = tu[3] if len(tu) > 3 else None
        if p < corte:
            continue
        r_cv = db.get(RegistroCurriculo, cid)
        if r_cv is None:
            continue
        atende_obrig, lacunas_obrig = _avaliar_requisitos_obrigatorios(
            r_cv.texto_indexado or "",
            requisitos_obrigatorios,
        )
        row = _montar_resposta_candidato(
            db,
            cid,
            p,
            justificativa=_justificativa_para_exibicao(
                db,
                desc,
                cid,
                p,
                just,
                via_gemini,
                (s100 if via_gemini else _score_local_0_100(p)),
            ),
            score_0_100=(s100 if via_gemini else _score_local_0_100(p)),
            atende_requisitos_obrigatorios=atende_obrig,
            lacunas_requisitos_obrigatorios=lacunas_obrig,
            lacunas_competencias_vaga=lacunas_comp_por_id.get(cid, []),
        )
        if row and atende_obrig:
            bons.append(row)
        elif row:
            row.justificativa = (
                f"Aproximação sem cobertura total dos obrigatórios ({', '.join(lacunas_obrig[:3])}). "
                f"{row.justificativa or ''}"
            ).strip()
            aproximados.append(row)
        if len(bons) >= n:
            break

    if bons:
        if len(bons) < n and aproximados:
            faltam = n - len(bons)
            bons.extend(aproximados[:faltam])
        if motor == "hibrido":
            msg_ok = msg_extra or "Análise híbrida concluída."
        elif via_gemini:
            msg_ok = (
                f"Análise concluída com {config.NOME_MODELO_GEMINI} (Google) — ordenação "
                f"com base na vaga e no texto indexado de cada PDF."
            )
        else:
            msg_ok = _mensagem_modo_local(chave_configurada)
        if msg_extra and motor != "hibrido":
            msg_ok = f"{msg_ok} {msg_extra}".strip()
        if requisitos_obrigatorios:
            msg_ok += (
                " Filtro de requisitos obrigatórios ativo; quando faltam candidatos aderentes, "
                "a API completa com os perfis mais próximos (marcados como aproximação)."
            )
        return AnaliseVagaResposta(
            mensagem_status=msg_ok,
            total_antes_corte=total_antes,
            resultados=bons,
        )

    pct_corte = int(round(corte * 100))
    motor = f" ({config.NOME_MODELO_GEMINI})" if via_gemini else " (reclassificador local)"
    return AnaliseVagaResposta(
        mensagem_status=(
            f"Nenhum candidato atingiu a afinidade mínima de {pct_corte}% "
            f"(corte {corte:.2f} em escala 0–1). Os perfis analisados não têm aderência "
            f"suficiente à vaga — não são sugeridos.{motor}{msg_extra}".strip()
        ),
        total_antes_corte=total_antes,
        resultados=[],
    )


app.include_router(router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config.HOSPEDEIRO_API,
        port=config.PORTA_API,
        reload=False,
    )
