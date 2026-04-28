from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from app.servicos.gemini_talentos import chave_disponivel, ordenar_talentos_por_gemini
from app.servicos.reclassificacao_vaga import (
    justificativa_resumo_local,
    ordenar_por_aderencia,
    ordenar_por_aderencia_lexical,
)
from app.servicos.pipeline_curriculo_vagas import ResultadoPipelineCurriculo, executar_pipeline_curriculo

app = FastAPI(
    title="Análise de Currículos (IA + Banco de talentos)",
    version="1.0.0",
    default_response_class=JSONResponse,
)

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
        "motor_classificacao": "gemini" if gem else "local",
        "chave_gemini_configurada": bool(gem),
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
        return "Gemini indisponível por quota (429); a usar reclassificação local."
    return "Gemini indisponível; a usar reclassificação local."


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
    if not via_gemini:
        return justificativa_resumo_local(
            desc_vaga,
            str(r.nome_candidato or "—"),
            r.texto_indexado or "",
            p,
        )
    t = (just_modelo or "").strip()
    if t:
        return t[:1500]
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
    )


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
    desc = requisicao.descricao_da_vaga
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

    if not candidatos:
        return AnaliseVagaResposta(
            mensagem_status="Não há texto indexado nos ficheiros. Reindexe reenviando os PDFs.",
            total_antes_corte=0,
            resultados=[],
        )

    cands_local: list[tuple[str, str]] = [(a, c) for a, _b, c in candidatos]
    msg_extra = ""
    via_gemini = False

    if bool(getattr(config, "APENAS_GEMINI", False)) and not chave_configurada:
        raise HTTPException(
            status_code=503,
            detail="Defina CHAVE_API_GEMINI ou GOOGLE_API_KEY no ficheiro .env (pasta backend).",
        )

    if chave_configurada:
        try:
            ordem = ordenar_talentos_por_gemini(config, desc, candidatos)
            total_antes = len(ordem)
            via_gemini = True
        except (RuntimeError, OSError, ValueError, MemoryError) as err:
            if bool(getattr(config, "APENAS_GEMINI", False)):
                raise HTTPException(status_code=502, detail=_mensagem_fallback_gemini(err)) from err
            try:
                ord2 = ordenar_por_aderencia(config, desc, cands_local)
            except (RuntimeError, OSError, ValueError, MemoryError):
                ord2 = ordenar_por_aderencia_lexical(config, desc, cands_local)
            total_antes = len(ord2)
            ordem = [(a, b, None, None) for a, b in ord2]
            msg_extra = " " + _mensagem_fallback_gemini(err)
    else:
        try:
            ord2 = ordenar_por_aderencia(config, desc, cands_local)
        except (RuntimeError, OSError, ValueError, MemoryError):
            ord2 = ordenar_por_aderencia_lexical(config, desc, cands_local)
        total_antes = len(ord2)
        ordem = [(a, b, None, None) for a, b in ord2]

    bons: list[CandidatoResultadoResposta] = []
    for tu in ordem:
        cid = tu[0]
        p = float(tu[1])
        just = tu[2] if len(tu) > 2 else None
        s100 = tu[3] if len(tu) > 3 else None
        if p < corte:
            continue
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
        )
        if row:
            bons.append(row)
        if len(bons) >= n:
            break

    if bons:
        if via_gemini:
            msg_ok = (
                f"Análise concluída com {config.NOME_MODELO_GEMINI} (Google) — ordenação "
                f"com base na vaga e no texto indexado de cada PDF."
            )
        else:
            msg_ok = _mensagem_modo_local(chave_configurada)
        if msg_extra:
            msg_ok = msg_ok + " " + msg_extra
        return AnaliseVagaResposta(
            mensagem_status=msg_ok,
            total_antes_corte=total_antes,
            resultados=bons,
        )

    bons2: list[CandidatoResultadoResposta] = []
    for tu in ordem[: max(n, 3)]:
        cid = tu[0]
        p = float(tu[1])
        just = tu[2] if len(tu) > 2 else None
        s100 = tu[3] if len(tu) > 3 else None
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
        )
        if row:
            bons2.append(row)
    if not bons2:
        return AnaliseVagaResposta(
            mensagem_status="Falha ao classificar. Verifique a chave Gemini, a quota, ou tente a reclassificação local outra vez.",
            total_antes_corte=0,
            resultados=[],
        )
    return AnaliseVagaResposta(
        mensagem_status=(
            f"Afinidade geral abaixo do corte. Melhor fila aproximada."
            f"{' (Gemini)' if via_gemini else ' (reclassificador local)'} {msg_extra}".strip()
        ),
        total_antes_corte=total_antes,
        resultados=bons2[:n],
    )


app.include_router(router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config.HOSPEDEIRO_API,
        port=config.PORTA_API,
        reload=False,
    )
