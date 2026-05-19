"""
Extração estruturada de currículos (estilo PyResparser).

Tenta usar a biblioteca pyresparser quando instalada; caso contrário, heurísticas PT-BR
sobre o texto já extraído do PDF (compatível com CVs em português).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.configuracao import Configuracao

_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_ANOS_EXP = re.compile(
    r"(\d{1,2})\s*(?:\+)?\s*anos?\s+(?:de\s+)?(?:experi[eê]ncia|exp\.?)",
    re.IGNORECASE,
)
_ANOS_EXP2 = re.compile(r"experi[eê]ncia\s+(?:de\s+)?(\d{1,2})\s*anos?", re.IGNORECASE)

_COMPETENCIAS_BUSCA: dict[str, tuple[str, ...]] = {
    "python": ("python", "django", "flask", "fastapi"),
    "javascript": ("javascript", "typescript", "node", "nodejs", "react", "vue", "angular"),
    "java": ("java", "spring", "kotlin"),
    "sql": ("sql", "postgres", "postgresql", "mysql", "sqlite", "oracle"),
    "dados": ("pandas", "numpy", "spark", "power bi", "tableau", "etl", "airflow"),
    "cloud": ("aws", "azure", "gcp", "docker", "kubernetes", "k8s"),
    "gestao": ("scrum", "agile", "kanban", "lideranca", "liderança", "gestão", "gestao"),
    "rh": ("recrutamento", "selecao", "seleção", "folha de pagamento", "departamento pessoal"),
    "vendas": ("vendas", "comercial", "crm", "negociacao", "negociação"),
    "marketing": ("marketing", "seo", "google ads", "redes sociais"),
    "excel": ("excel", "planilhas", "vlookup", "power query"),
    "logistica": ("logistica", "logística", "estoque", "almoxarifado", "wms", "expedição"),
}


def _normalizar(s: str) -> str:
    return (s or "").lower()


def _extrair_heuristica_pt(texto: str) -> dict[str, Any]:
    t = texto or ""
    tn = _normalizar(t)
    habilidades: list[str] = []
    for rotulo, variantes in _COMPETENCIAS_BUSCA.items():
        if any(v in tn for v in variantes):
            habilidades.append(rotulo)

    anos: float | None = None
    for pat in (_ANOS_EXP, _ANOS_EXP2):
        m = pat.search(t)
        if m:
            try:
                anos = float(m.group(1))
                break
            except (TypeError, ValueError):
                pass

    emails = list(dict.fromkeys(_EMAIL.findall(t)))[:3]
    empresas: list[str] = []
    for linha in t.splitlines():
        ln = linha.strip()
        if 3 <= len(ln) <= 80 and re.search(
            r"\b(ltda|s\.?a\.?|inc|corp|consultoria|tecnologia|servi[cç]os)\b",
            ln,
            re.I,
        ):
            empresas.append(ln[:120])
    empresas = list(dict.fromkeys(empresas))[:8]

    return {
        "fonte": "heuristica_pt",
        "habilidades": habilidades,
        "experiencia_anos": anos,
        "empresas": empresas,
        "formacao": [],
        "email_detectado": emails[0] if emails else None,
        "emails": emails,
        "nome_detectado": None,
        "designacao": None,
    }


def _via_pyresparser(caminho_pdf: Path) -> dict[str, Any] | None:
    try:
        from pyresparser import ResumeParser  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        bruto = ResumeParser(str(caminho_pdf)).get_extracted_data()
    except Exception:
        return None
    if not isinstance(bruto, dict):
        return None
    skills = bruto.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    elif not isinstance(skills, list):
        skills = []
    exp = bruto.get("total_experience")
    anos: float | None = None
    if isinstance(exp, (int, float)):
        anos = float(exp)
    elif isinstance(exp, str):
        m = re.search(r"(\d+(?:\.\d+)?)", exp)
        if m:
            anos = float(m.group(1))
    return {
        "fonte": "pyresparser",
        "habilidades": [str(s)[:80] for s in skills[:40]],
        "experiencia_anos": anos,
        "empresas": [str(x)[:120] for x in (bruto.get("company_names") or [])[:12] if x],
        "formacao": [str(x)[:120] for x in (bruto.get("degree") or [])[:8] if x],
        "email_detectado": (bruto.get("email") or [None])[0]
        if isinstance(bruto.get("email"), list)
        else bruto.get("email"),
        "emails": bruto.get("email") if isinstance(bruto.get("email"), list) else [],
        "nome_detectado": bruto.get("name"),
        "designacao": bruto.get("designation"),
    }


def extrair_dados_estruturados(
    config: "Configuracao",
    caminho_pdf: Path,
    texto_plano: str,
) -> dict[str, Any]:
    """Retorna dicionário serializável para guardar em JSON no banco."""
    usar = bool(getattr(config, "USAR_PYRESPARSER", True))
    dados: dict[str, Any] | None = None
    if usar and caminho_pdf.is_file():
        dados = _via_pyresparser(caminho_pdf)
    if dados is None:
        dados = _extrair_heuristica_pt(texto_plano)
    else:
        heur = _extrair_heuristica_pt(texto_plano)
        hab = list(dict.fromkeys([*(dados.get("habilidades") or []), *(heur.get("habilidades") or [])]))
        dados["habilidades"] = hab[:50]
        if not dados.get("email_detectado") and heur.get("email_detectado"):
            dados["email_detectado"] = heur["email_detectado"]
    return dados


def dados_estruturados_de_json(bruto: str | None) -> dict[str, Any]:
    if not bruto:
        return {}
    try:
        o = json.loads(bruto)
        return o if isinstance(o, dict) else {}
    except json.JSONDecodeError:
        return {}


def serializar_dados_estruturados(dados: dict[str, Any]) -> str:
    return json.dumps(dados, ensure_ascii=False)
