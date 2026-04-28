"""
Análise com Google Gen AI (google.genai).

- **Modo lote (padrão)**: 1 requisição com todos os candidatos = rápido, poupa cota.
- **Modo sequencial** (`GEMINI_LOTE=false`): 1 requisição por CV.
- **429 / cota**: re-lança para a API geral ativar o classificador local (sem 0% fictício em massa).
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.configuracao import Configuracao

_JSON_UM = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\{.*\}", re.DOTALL)
PREFIXO_QUOTA = "GEMINI_QUOTA:"


def chave_disponivel(config: "Configuracao") -> bool:
    return bool((getattr(config, "CHAVE_API_GEMINI", None) or "").strip())


def _pontuacao_0_1_e_score100(score: object) -> tuple[float, int]:
    try:
        s = int(round(float(score)))
    except (TypeError, ValueError):
        s = 0
    s = max(0, min(100, s))
    return s / 100.0, s


def _e_quota_ou_429(e: BaseException) -> bool:
    s = str(e).lower()
    if "429" in s:
        return True
    if "quota" in s and "exceed" in s:
        return True
    if "resource" in s and "exhaust" in s:
        return True
    if "free_tier" in s and "limit" in s:
        return True
    return False


def _e_overload_503(e: BaseException) -> bool:
    s = str(e).lower()
    if "503" in s or "unavailable" in s or "overloaded" in s:
        return True
    return "resource" in s and "exhaust" in s


def _extrair_json_obj(resposta: str) -> dict | None:
    if not resposta:
        return None
    resposta = resposta.replace("```json", "").replace("```", "").strip()
    m = _JSON_UM.search(resposta)
    alvo = m.group(0) if m else resposta
    try:
        o = json.loads(alvo)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        a = alvo.find("{")
        b = alvo.rfind("}")
        if 0 <= a < b:
            try:
                o = json.loads(alvo[a : b + 1])
                return o if isinstance(o, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _texto_resposta(resp: object) -> str:
    if resp is None:
        return ""
    t = getattr(resp, "text", None)
    if t:
        return str(t).strip()
    if getattr(resp, "candidates", None):
        partes: list[str] = []
        for c in resp.candidates or []:
            ccontent = getattr(c, "content", None)
            if ccontent and getattr(ccontent, "parts", None):
                for p in ccontent.parts:
                    if getattr(p, "text", None):
                        partes.append(p.text)
        return "\n".join(partes).strip()
    return ""


def _tr(texto: str, lim: int) -> str:
    t = (texto or "").strip()
    if len(t) <= lim:
        return t
    return t[: lim - 1] + "…"


def _gerar_novo(chave: str, modelo: str, prompt: str) -> str:
    from google import genai  # type: ignore[import-untyped]
    from google.genai import types  # type: ignore[import-untyped]

    try:
        cliente = genai.Client(api_key=chave)
        cfg = types.GenerateContentConfig(
            max_output_tokens=8192,
            temperature=0.1,
        )
        resp = cliente.models.generate_content(
            model=modelo,
            contents=prompt,
            config=cfg,
        )
    except Exception as e:  # noqa: BLE001
        if _e_quota_ou_429(e):
            raise RuntimeError(
                f"{PREFIXO_QUOTA} Cota ou limite da API Gemini. Aguarde 1 min., altere o modelo em "
                f"NOME_MODELO_GEMINI, ou a API cairá no classificador local. Detalhe: {e!s}"
            ) from e
        raise
    return _texto_resposta(resp)


def ordenar_talentos_por_gemini_lote(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str, str]],
) -> list[tuple[str, float, str | None, int | None]]:
    if not candidatos:
        return []
    ch = (getattr(config, "CHAVE_API_GEMINI", None) or "").strip()
    if not ch:
        return []

    lim = int(getattr(config, "TRECHO_CANDIDATO_GEMINI", 3500))
    n = len(candidatos)
    blocos: list[str] = []
    for i, (cid, nome, texto) in enumerate(candidatos, 1):
        tre = _tr(texto, lim)
        blocos.append(
            f"### CANDIDATO {i}\n"
            f"id_candidato: {cid}\n"
            f"Nome (registo): {nome}\n"
            f"Texto do currículo:\n{tre}\n"
        )

    vaga = (descricao_vaga or "").strip()
    prompt = f"""
Atue como recrutador técnico. Para cada candidato, compare o CV à VAGA.
Atribua a cada um um "score" inteiro de 0 a 100 (0 = sem ligação, 100 = muito adequado) e
uma "justificativa" objetiva em português (1 a 2 frases, até 260 carateres), baseada só no texto do CV.

Regras de aderência (aplique sempre):
- O score mede a adequação DIRETA da experiência comprovada no CV à função, tarefas e requisitos descritos
  na vaga. Não basta competência genérica ou setor “parecido”.
- Se a experiência principal do candidato for noutro tipo de função, setor de atuação ou conjunto de
  responsabilidades claramente diferente do que a vaga exige, a nota deve ser baixa (muitas vezes abaixo
  de 30), mesmo que o CV seja sólido em outro domínio.
- Só atribua notas altas quando o CV mostrar, com clareza, prática profissional alinhada ao que a vaga
  pede (cargo, atividades, especialidade).
- A justificativa deve citar pelo menos 2 evidências concretas do currículo (ex.: ferramentas, tarefas,
  anos de experiência, tipo de operação, segmento), e mencionar explicitamente o motivo da escolha.

VAGA:
{vaga}

{"".join(blocos)}

Responda SOMENTE com JSON puro (sem ```):
{{
  "avaliacoes": [
    {{"id_candidato": "uuid-igual-ao-listado", "score": 75, "justificativa": "1-2 frases com evidências do CV"}}
  ]
}}
O array "avaliacoes" deve conter exatamente {n} entradas, com cada id_candidato listado acima, sem repetir.
""".strip()

    modelos: list[str] = []
    for m in [
        (getattr(config, "NOME_MODELO_GEMINI", None) or "gemini-2.0-flash").strip(),
        (getattr(config, "NOME_MODELO_GEMINI_BACKUP", None) or "gemini-1.5-flash").strip(),
    ]:
        if m and m not in modelos:
            modelos.append(m)

    tent = max(1, int(getattr(config, "GEMINI_TENTATIVAS_POR_MODELO", 1)))
    pausa_503 = max(1, int(getattr(config, "GEMINI_PAUSA_503_SEGUNDOS", 2)))
    last_info = ""

    for mod in modelos:
        for _ in range(tent):
            try:
                raw = _gerar_novo(ch, mod, prompt)
            except RuntimeError as e:
                s = str(e)
                if PREFIXO_QUOTA in s:
                    raise
                if _e_overload_503(e):
                    last_info = s
                    time.sleep(float(pausa_503))
                    continue
                last_info = s
                break
            if not raw:
                last_info = "resposta vazia do modelo"
                continue
            js = _extrair_json_obj(raw)
            if not js:
                last_info = "json inválido"
                continue
            avs = [x for x in (js.get("avaliacoes") or []) if isinstance(x, dict)]
            ids_por = {a[0] for a in candidatos}
            mapeado: dict[str, tuple[float, str | None, int]] = {}
            for x in avs:
                cid = str(x.get("id_candidato", "")).strip()
                if not cid or cid not in ids_por:
                    continue
                p01, s100 = _pontuacao_0_1_e_score100(x.get("score", 0))
                jt = x.get("justificativa")
                j = str(jt)[:500] if jt else None
                mapeado[cid] = (p01, j, s100)
            faltou = [cid for cid, _, _ in candidatos if cid not in mapeado]
            for cid in faltou:
                mapeado[cid] = (
                    0.0,
                    "Id não retornado na mesma ronda; confira tamanho do lote em TRECHO_CANDIDATO_GEMINI.",
                    0,
                )
            if not mapeado:
                last_info = "nenhum id reconhecido na resposta"
                continue
            out: list[tuple[str, float, str | None, int | None]] = []
            for cid, _, _ in candidatos:
                t = mapeado[cid]
                out.append((cid, t[0], t[1], t[2]))
            out.sort(key=lambda x: (-(x[1] or 0.0), x[0]))
            return out
    raise RuntimeError(
        f"Gemini (lote) não concluiu. {last_info} — confira o nome do modelo (ex.: gemini-2.0-flash) em .env"
    )


def _analisar_um_cv_direto(
    config: "Configuracao",
    chave: str,
    vaga: str,
    id_cand: str,
    nome: str,
    texto_cv: str,
) -> dict | None:
    lim = int(getattr(config, "TRECHO_CANDIDATO_GEMINI", 3500))
    tre = (texto_cv or "")[:lim]
    vaga_l = (vaga or "").strip()
    pr = f"""
Recrutador técnico. Avalie o CV para a vaga. id_candidato (UUID) deve ser: {id_cand}
Nome no sistema: {nome}
O score 0-100 mede a adequação DIRETA: experiência comprovada alinhada à função e requisitos da vaga.
Rejeite com nota baixa perfis cuja experiência seja noutro tipo de função ou setor, mesmo que o CV seja
forte fora do pedido.
A justificativa deve ser direta, personalizada para este candidato, e citar pelo menos 2 evidências
observáveis no CV que expliquem a nota.

VAGA:
{vaga_l}

TEXTO DO CV:
{tre}

Responda SOMENTE JSON:
{{"id_candidato": "{id_cand}", "score": <0-100 inteiro>, "justificativa": "1-2 frases com 2 evidências concretas do CV"}}
""".strip()

    modelos: list[str] = []
    for m in [
        (getattr(config, "NOME_MODELO_GEMINI", None) or "gemini-2.0-flash").strip(),
        (getattr(config, "NOME_MODELO_GEMINI_BACKUP", None) or "gemini-1.5-flash").strip(),
    ]:
        if m and m not in modelos:
            modelos.append(m)
    tent = max(1, int(getattr(config, "GEMINI_TENTATIVAS_POR_MODELO", 1)))
    pausa_503 = max(1, int(getattr(config, "GEMINI_PAUSA_503_SEGUNDOS", 2)))

    for mod in modelos:
        for _ in range(tent):
            try:
                raw = _gerar_novo(chave, mod, pr)
            except RuntimeError as e:
                if PREFIXO_QUOTA in str(e):
                    raise
                if _e_overload_503(e):
                    time.sleep(float(pausa_503))
                    continue
                break
            if not raw:
                time.sleep(0.25)
                continue
            js = _extrair_json_obj(raw)
            if not js or "score" not in js:
                continue
            return js
    return None


def _sequencial_ordenar(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str, str]],
) -> list[tuple[str, float, str | None, int | None]]:
    ch = (getattr(config, "CHAVE_API_GEMINI", None) or "").strip()
    if not ch or not candidatos:
        return []
    pausa_cv = max(0, int(getattr(config, "GEMINI_PAUSA_ENTRE_CVS_SEGUNDOS", 0)))
    n = len(candidatos)
    out: list[tuple[str, float, str | None, int | None]] = []
    for idx, (cid, nome, texto) in enumerate(candidatos):
        try:
            js = _analisar_um_cv_direto(config, ch, descricao_vaga, cid, nome, texto)
        except RuntimeError:
            raise
        if not js:
            p01, s00, j = 0.0, None, "Falha ao obter resposta (modelo ou formatação)."
        else:
            p01, s1 = _pontuacao_0_1_e_score100(js.get("score", 0))
            s00 = s1
            jt = js.get("justificativa")
            j = str(jt)[:500] if jt else None
        out.append((cid, p01, j, s00))
        if pausa_cv and idx < n - 1:
            time.sleep(float(pausa_cv))
    out.sort(key=lambda x: (-(x[1] or 0.0), x[0]))
    return out


def ordenar_talentos_por_gemini(
    config: "Configuracao",
    descricao_vaga: str,
    candidatos: list[tuple[str, str, str]],
) -> list[tuple[str, float, str | None, int | None]]:
    lote = bool(getattr(config, "GEMINI_LOTE", True))
    if lote and candidatos:
        return ordenar_talentos_por_gemini_lote(config, descricao_vaga, candidatos)
    return _sequencial_ordenar(config, descricao_vaga, candidatos)
