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
        itens: list[dict[str, Any]] = []
        ids_ = (resultado.get("ids") or [[]])[0] or []
        dists = (resultado.get("distances") or [[]])[0] or []
        mets = (resultado.get("metadatas") or [[]])[0] or []
        for i, ident in enumerate(ids_):
            dist = float(dists[i]) if i < len(dists) else 1.0
            afinidade = max(0.0, 1.0 - dist)
            itens.append(
                {
                    "candidato_id": ident,
                    "pontuacao_afinidade": round(afinidade, 4),
                    "metadados": mets[i] or {},
                }
            )
        return itens
