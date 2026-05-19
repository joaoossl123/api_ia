"""
Motor de afinidade: Chroma + Sentence-Transformers multilíngue.
Distância em cosseno → pontuação de afinidade; corte mínimo reduz falsos positivos.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

from app.configuracao import Configuracao, resolver_caminho


NOME_COLECAO = "banco_talentos_candidatos"


def _primeira_batch_query(val: Any) -> list[Any]:
    """Normaliza ids/distâncias/metadados do Chroma (list, tuple, set, ndarray)."""
    if val is None:
        return []
    if hasattr(val, "tolist"):
        try:
            val = val.tolist()
        except Exception:
            pass
    if isinstance(val, set):
        return list(val)
    if isinstance(val, (list, tuple)):
        if len(val) == 0:
            return []
        primeiro = val[0]
        if isinstance(primeiro, set):
            return list(primeiro)
        if hasattr(primeiro, "tolist"):
            try:
                primeiro = primeiro.tolist()
            except Exception:
                pass
        if isinstance(primeiro, (list, tuple)):
            return list(primeiro)
        return list(val)
    return []


class BancoVetorialTalentos:
    def __init__(self, config: Configuracao) -> None:
        self._config = config
        pasta = resolver_caminho(config, config.PASTA_BANCO_VETORIAL)
        pasta.mkdir(parents=True, exist_ok=True)

        self._embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=config.NOME_MODELO_EMBEDDINGS
        )
        self._cliente = chromadb.PersistentClient(path=str(pasta))
        self._colecao = self._cliente.get_or_create_collection(
            name=NOME_COLECAO,
            metadata={"hnsw:space": "cosine"},
            embedding_function=self._embedder,
        )

    def inserir_curriculo(
        self,
        texto: str,
        metadados: dict[str, Any],
    ) -> str:
        ident = str(metadados.get("candidato_id") or uuid.uuid4())
        md = {k: str(v) for k, v in metadados.items() if v is not None}
        self._colecao.upsert(
            ids=[ident],
            documents=[texto],
            metadatas=[md],
        )
        return ident

    def remover(self, ident: str) -> None:
        try:
            self._colecao.delete(ids=[ident])
        except Exception:
            pass

    def buscar_afinidade(
        self,
        descricao_vaga: str,
        limite: int,
    ) -> list[dict[str, Any]]:
        n = min(max(1, limite), 50)
        if not (descricao_vaga or "").strip():
            return []
        n_docs = int(self._colecao.count())
        if n_docs == 0:
            return []
        n_busca = min(n, n_docs)
        if n_busca < 1:
            return []
        try:
            resultado = self._colecao.query(
                query_texts=[descricao_vaga],
                n_results=n_busca,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return []
        if not isinstance(resultado, dict) and hasattr(resultado, "keys"):
            try:
                resultado = dict(resultado)
            except Exception:
                return []
        elif not isinstance(resultado, dict):
            return []
        itens: list[dict[str, Any]] = []
        ids_ = _primeira_batch_query(resultado.get("ids"))
        dists = _primeira_batch_query(resultado.get("distances"))
        mets = _primeira_batch_query(resultado.get("metadatas"))
        for i, ident in enumerate(ids_):
            dist = float(dists[i]) if i < len(dists) else 1.0
            afinidade = max(0.0, 1.0 - dist)
            md: dict[str, Any] = {}
            if i < len(mets):
                mi = mets[i]
                if isinstance(mi, dict):
                    md = mi
            itens.append(
                {
                    "candidato_id": ident,
                    "pontuacao_afinidade": round(afinidade, 4),
                    "metadados": md,
                }
            )
        return itens
