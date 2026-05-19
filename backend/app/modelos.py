"""Modelos Pydantic — nomes em português para a API (contrato estável)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EnvioVagaRequisicao(BaseModel):
    descricao_da_vaga: str = Field(
        ...,
        min_length=10,
        description="Texto completo da vaga, requisitos, stack e perfil desejado.",
    )
    quantidade_sugerida: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Máximo de candidatos a retornar, ordenado por afinidade.",
    )
    requisitos_obrigatorios: list[str] = Field(
        default_factory=list,
        description="Lista de requisitos eliminatórios da vaga.",
    )
    requisitos_desejaveis: list[str] = Field(
        default_factory=list,
        description="Lista de requisitos diferenciais (não eliminatórios).",
    )
    motor_analise: Literal["padrao", "hibrido"] = Field(
        default="padrao",
        description=(
            "padrao: Chroma + cross-encoder/Gemini. "
            "hibrido: PyResparser + Resume Matcher (TF-IDF/semântico/skills) + Gemini opcional."
        ),
    )


class CandidatoResultadoResposta(BaseModel):
    id_candidato: str
    nome_candidato: str
    email: Optional[str] = None
    nome_arquivo_original: str
    pontuacao_afinidade: float
    score_0_100: Optional[int] = Field(
        default=None,
        description="Pontuação 0-100 (IA), se disponível; afinidade 0-1 aproximada a score/100.",
    )
    justificativa: Optional[str] = Field(
        default=None,
        description="Porque este candidato foi (ou não) selecionado, com referência concreta ao CV e à vaga.",
    )
    atende_requisitos_obrigatorios: Optional[bool] = Field(
        default=None,
        description="Indica se o currículo cobriu os requisitos obrigatórios informados na vaga.",
    )
    lacunas_requisitos_obrigatorios: list[str] = Field(
        default_factory=list,
        description="Requisitos obrigatórios sem evidência clara no currículo.",
    )
    lacunas_competencias_vaga: list[str] = Field(
        default_factory=list,
        description="Competências da vaga sem evidência no CV (motor híbrido / Resume Matcher).",
    )
    comentario_padrao: str = "Ordenado por afinidade com a vaga. Leia a justificativa e o documento do candidato."


class AnaliseVagaResposta(BaseModel):
    mensagem_status: str
    total_antes_corte: int
    resultados: list[CandidatoResultadoResposta]


class CurriculoResumo(BaseModel):
    id: str
    nome_candidato: str
    email: Optional[str] = None
    nome_arquivo: str
    criado_em: datetime
    trecho_vista_previa: Optional[str] = None


class MensagemSimples(BaseModel):
    mensagem: str


class VagaComparacaoItem(BaseModel):
    nome: str
    descricao: str = Field(
        default="",
        description="Texto da vaga usado no embedding; quanto mais detalhado, melhor a aderência.",
    )


class RequisicaoCompatibilidadeVagas(BaseModel):
    texto: str = Field(
        ...,
        min_length=20,
        description="Texto completo do currículo (mín. 20 caracteres).",
    )
    vagas: list[VagaComparacaoItem] | None = Field(
        default=None,
        description="Lista opcional; se vazia, usa o conjunto de referência interno (Backend, Dados, etc.).",
    )


class SpacyEntidadeResumo(BaseModel):
    texto: str
    rotulo: str


class AderenciaVagaItem(BaseModel):
    vaga_titulo: str
    percentual: int
    pontuacao_0_1: float


class PerfilClassificadoItem(BaseModel):
    rotulo: str
    percentual: int
    pontuacao_0_1: float


class AnaliseCompatibilidadeVagasResposta(BaseModel):
    spacy_resumo: str
    spacy_entidades: list[SpacyEntidadeResumo]
    aderencia_vagas: list[AderenciaVagaItem]
    classificacao_perfil: list[PerfilClassificadoItem]
    trecho_texto: str
    detalhe_erro: str | None = None
    mensagem: str
    motor_perfil: str = Field(
        default="nli",
        description="nli: Transformers (zero-shot NLI); st_semantica: Sentence-Transformers (cos-sim) como fallback",
    )
