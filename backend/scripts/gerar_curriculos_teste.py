from pathlib import Path
import random

import fitz


def main() -> None:
    random.seed(42)
    base = Path("c:/Users/joazi/OneDrive/Documentos/Api-ia/backend/curriculos_teste_100")
    base.mkdir(parents=True, exist_ok=True)

    vagas = [
        ("Desenvolvedor Python", "Python, FastAPI, Linux, APIs REST, SQL, Docker"),
        ("Desenvolvedor Frontend React", "React, TypeScript, JavaScript, HTML, CSS"),
        ("Analista de Dados", "SQL, Python, Power BI, ETL, Estatistica"),
        ("DevOps Junior", "Linux, Docker, CI/CD, Kubernetes, AWS"),
        ("Suporte Tecnico", "Atendimento, Redes, Windows, Linux, ITIL"),
        ("QA Tester", "Testes manuais, Cypress, Postman, automacao"),
        ("Assistente Administrativo", "Excel, rotinas administrativas, comunicacao"),
        ("Designer UX/UI", "Figma, prototipacao, pesquisa com usuario"),
    ]

    nomes = [
        "Ana",
        "Bruno",
        "Carlos",
        "Daniela",
        "Eduardo",
        "Fernanda",
        "Gabriel",
        "Helena",
        "Igor",
        "Juliana",
        "Kaique",
        "Larissa",
        "Marcos",
        "Natalia",
        "Otavio",
        "Patricia",
        "Rafael",
        "Sara",
        "Tiago",
        "Vanessa",
    ]
    sobrenomes = [
        "Silva",
        "Souza",
        "Oliveira",
        "Santos",
        "Lima",
        "Costa",
        "Pereira",
        "Gomes",
        "Ribeiro",
        "Almeida",
    ]

    manifest = ["arquivo;nome;cargo_alvo;tipo"]

    for i in range(1, 101):
        nome = f"{random.choice(nomes)} {random.choice(sobrenomes)}"

        if i <= 40:
            cargo, skills = vagas[0]
            tipo = "igual_python"
            exp = (
                f"Experiencia de {random.randint(1,5)} anos em desenvolvimento de sistemas usando "
                f"{skills}. Atuacao com microsservicos e integracoes."
            )
        elif i <= 55:
            cargo, skills = vagas[0]
            tipo = "parcial_python"
            skill_parcial = random.choice(["Python", "APIs REST", "Linux"])
            exp = (
                "Experiencia geral em TI com foco parcial em "
                f"{skill_parcial} e suporte a projetos de software."
            )
        else:
            cargo, skills = random.choice(vagas[1:])
            tipo = "distinto"
            exp = f"Experiencia profissional voltada para {cargo.lower()} com foco em {skills}."

        formacao = random.choice(
            [
                "Graduacao em Sistemas de Informacao",
                "Tecnologo em Analise e Desenvolvimento de Sistemas",
                "Curso tecnico em Informatica",
                "Graduacao em Administracao",
            ]
        )

        texto = (
            f"Curriculo de {nome}\n"
            f"Cargo de interesse: {cargo}\n"
            f"Resumo: {exp}\n"
            f"Competencias: {skills}\n"
            f"Formacao: {formacao}.\n"
            "Idiomas: Portugues avancado.\n"
            "Projetos: Participacao em projetos colaborativos com boas praticas e entrega de resultados.\n"
        )

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), texto, fontsize=11)
        arquivo = f"curriculo_teste_{i:03d}.pdf"
        doc.save(base / arquivo)
        doc.close()

        manifest.append(f"{arquivo};{nome};{cargo};{tipo}")

    (base / "manifesto_curriculos.csv").write_text("\n".join(manifest), encoding="utf-8")
    print(f"Gerados 100 curriculos em: {base}")


if __name__ == "__main__":
    main()
