"""Persistência de metadados dos currículos; arquivos PDF e índice vetorial associado."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, DateTime, String, Text, create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.configuracao import Configuracao, obter_caminho_backend, resolver_caminho

Base = declarative_base()


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


class RegistroCurriculo(Base):
    __tablename__ = "registros_curriculos"

    id = Column(String(36), primary_key=True)
    nome_candidato = Column(String(200), nullable=False)
    email = Column(String(200), nullable=True)
    nome_arquivo_original = Column(String(500), nullable=False)
    caminho_relativo_pdf = Column(String(1000), nullable=False)
    texto_indexado = Column(Text, nullable=False)
    resumo_vista_previa = Column(String(2000), nullable=True)
    dados_estruturados_json = Column(Text, nullable=True)
    criado_em = Column(DateTime(timezone=True), default=_agora_utc, nullable=False)


_motor: object | None = None
_Sessao: sessionmaker | None = None


def init_banco(c: Configuracao) -> None:
    global _motor, _Sessao
    cam = resolver_caminho(c, Path(c.NOME_BANCO_SQLITE))
    cam.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{cam.as_posix()}"
    _motor = create_engine(
        url,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=_motor)
    _migrar_sqlite(_motor)
    _Sessao = sessionmaker(autocommit=False, autoflush=False, bind=_motor)


def _migrar_sqlite(motor: object) -> None:
    """Adiciona colunas novas em bases SQLite já existentes."""
    with motor.connect() as conn:  # type: ignore[union-attr]
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(registros_curriculos)"))}
        if "dados_estruturados_json" not in cols:
            conn.execute(
                text("ALTER TABLE registros_curriculos ADD COLUMN dados_estruturados_json TEXT")
            )
            conn.commit()


def abrir_sessao() -> Session:
    if _Sessao is None:
        raise RuntimeError("Banco nao inicializado. Chame init_banco() antes.")
    return _Sessao()
