"""Configurações padrão do analisador de talentos (português, uso interno)."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracao(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Servidor
    NOME_SISTEMA: str = "Análise de Currículos com IA"
    MENSAGEM_BOAS_VINDAS: str = (
        "Banco de talentos: análise da vaga com Google Gemini 2.0 Flash Lite; "
        "o índice local limita a lista quando o banco é muito grande."
    )
    HOSPEDEIRO_API: str = "0.0.0.0"
    PORTA_API: int = 8000
    ORIGEM_PERMITIDA_FRONTEND: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:5174,http://localhost:5174"
    )

    # Caminhos (relativos à pasta backend/)
    PASTA_CURRICULOS: Path = Path("curriculos_arquivos")
    PASTA_BANCO_VETORIAL: Path = Path("armazenamento/vectordb")
    NOME_BANCO_SQLITE: str = "armazenamento/banco_talentos.db"

    # Modelo de embeddings (retrieval inicial) — multilíngue
    NOME_MODELO_EMBEDDINGS: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # Classificação multilingue (zero-shot) — perfil/área do currículo
    # NLI zero-shot (HuggingFace); ex.: valhalla/distilbart-mnli-12-3 (padrão em muitas doc.)
    NOME_MODELO_ZERO_SHOT: str = "valhalla/distilbart-mnli-12-3"
    PIPELINE_TAMANHO_SPA: int = 20_000
    PIPELINE_TAMANHO_VETOR_ST: int = 10_000
    PIPELINE_TAMANHO_ZERO_SHOT: int = 3_000
    # Reclassificação local (fallback se não houver chave Gemini)
    NOME_MODELO_RECLASSIFICADOR: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    TAMANHO_TRECHO_PDF: int = 12_000

    # Google Gemini (SDK google.genai). Por defeito: 1 req. em lote = rápido. Modelos: ver AI Studio
    NOME_MODELO_GEMINI: str = "gemini-2.0-flash-lite"
    NOME_MODELO_GEMINI_BACKUP: str = "gemini-flash-lite-latest"
    TRECHO_CANDIDATO_GEMINI: int = 3_500
    # true: uma chamada com todos os CVs (recomendado, menos cota e mais rápido)
    GEMINI_LOTE: bool = True
    GEMINI_TENTATIVAS_POR_MODELO: int = 3
    GEMINI_PAUSA_503_SEGUNDOS: int = 5
    # Apenas se GEMINI_LOTE=False (1 CV por chamada)
    GEMINI_PAUSA_ENTRE_CVS_SEGUNDOS: int = 0
    CHAVE_API_GEMINI: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CHAVE_API_GEMINI",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ),
    )
    # true = força análise só com Gemini; false = se não houver chave, usa modelo local
    APENAS_GEMINI: bool = False

    # Pesos do score final (0..1): cruz (par a par) + alinhamento de termos da vaga no PDF
    PESO_CROSS_ENCODER: float = 0.70
    PESO_COBERTURA_LEXICAL: float = 0.30

    # Se houver no máximo este número de CVs, todos entram no reclassificador (máxima aderência)
    MAX_CURRICULOS_TODOS_NA_ANALISE: int = 64
    # Limita custo do reclassificador local em bases grandes (escala para milhares de CVs)
    MAX_CANDIDATOS_RECLASSIFICACAO_LOCAL: int = 120
    POOL_BUSCA_VETORIAL_MIN: int = 24
    POOL_BUSCA_VETORIAL_MULTIPLICADOR: int = 6
    POOL_SUPLEMENTAR_MAXIMO: int = 120

    # Busca: corte após reclassificação (escala combinada, tendencialmente 0,25–0,95)
    LIMITE_PADRAO_CANDIDATOS: int = 15
    CORTE_PONTUACAO_MINIMA: float = 0.35
    RETORNAR_MELHOR_NUMERO: int = 5


def obter_caminho_backend() -> Path:
    return Path(__file__).resolve().parent.parent


def resolver_caminho(_: Configuracao, rel: Path) -> Path:
    base = obter_caminho_backend()
    return (base / rel).resolve()
