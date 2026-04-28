from __future__ import annotations

import csv
from pathlib import Path

import requests


def main() -> None:
    base = Path("c:/Users/joazi/OneDrive/Documentos/Api-ia/backend/curriculos_teste_100")
    manifest = base / "manifesto_curriculos.csv"
    url = "http://127.0.0.1:8000/api/curriculos/enviar"

    rows = list(csv.DictReader(manifest.open(encoding="utf-8"), delimiter=";"))
    total = len(rows)
    ok = 0
    falhas: list[tuple[str, str, str]] = []

    sessao = requests.Session()
    for i, row in enumerate(rows, 1):
        arq = base / row["arquivo"]
        email_nome = row["nome"].lower().replace(" ", ".")
        data = {
            "candidato": row["nome"],
            "email": f"{email_nome}.{i}@teste.local",
        }
        with arq.open("rb") as f:
            files = {"arquivo": (arq.name, f, "application/pdf")}
            try:
                resp = sessao.post(url, data=data, files=files, timeout=90)
                if resp.status_code == 200:
                    ok += 1
                else:
                    falhas.append((arq.name, str(resp.status_code), resp.text[:200]))
            except Exception as exc:  # noqa: BLE001
                falhas.append((arq.name, "ERR", str(exc)[:200]))
        print(f"[{i}/{total}] enviado {arq.name}")

    print(f"RESUMO total={total} sucesso={ok} falhas={len(falhas)}")
    if falhas:
        print("FALHAS (primeiras 10):")
        for item in falhas[:10]:
            print(item)


if __name__ == "__main__":
    main()
