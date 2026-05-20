# Backups do projeto — como restaurar

## Importante sobre “ontem às 19:00”

O **Git não tinha commit** de 18/05/2026 às 19:00. O último commit no repositório é de **29/04/2026** (`73db22f`).

Tudo o que foi feito depois (incluindo alterações de ontem) estava **só no disco**, sem commit — por isso não é possível recuperar essa hora exata só com Git.

Para recuperar ficheiros de **18/05 ~19:00** com precisão:

1. Abra a pasta do projeto no **Explorador de Ficheiros**
2. Clique direito em `Api-IA_PROJETOMULHERES` → **Histórico de versões** (OneDrive)
3. Escolha a versão da data/hora desejada e restaure

---

## O que foi criado agora

| Backup | Onde | Conteúdo |
|--------|------|----------|
| **Requisitos básicos/desejáveis** | Branch `backup/requisitos-vaga-2026-05-19` + pasta `..\Api-IA_PROJETOMULHERES_BACKUP_REQUISITOS` | Base `backup/antes-precisao-ia` + **só** UI e textos de requisitos na vaga (commit `6745607`) |
| **Último commit Git** | Branch `backup/ultimo-commit-git` | Código de 29/04/2026 (commit `73db22f`) — **sem** API de requisitos separados |
| **Branch** `backup/versao-completa-2026-05-19` | Só no Git | Tudo: motor híbrido, integração, precisão IA, etc. |
| **Branch** `backup/antes-precisao-ia` | Só no Git | Motor híbrido, corte de vaga, Gemini 429 — **sem** UI de requisitos nem calibração extra de precisão |

---

## Backup de requisitos (recomendado para o pedido do grupo)

**Branch:** `backup/requisitos-vaga-2026-05-19`  
**Pasta cópia (worktree):** `Api-IA_PROJETOMULHERES_BACKUP_REQUISITOS` (ao lado desta pasta no Documentos)

### O que inclui (apenas requisitos)

- **Backend** (já na base): `requisitos_obrigatorios`, `requisitos_desejaveis`, avaliação e lacunas em `POST /api/vaga/analise`
- **Frontend** (commit desta branch): campos “Requisitos básicos” e “Requisitos desejáveis”, envio à API e exibição de lacunas nos resultados
- **Modelos:** descrições dos campos em `backend/app/modelos.py`

### Como usar

```powershell
cd Api-IA_PROJETOMULHERES
git checkout backup/requisitos-vaga-2026-05-19
```

Ou trabalhe diretamente na pasta irmã:

```powershell
cd ..\Api-IA_PROJETOMULHERES_BACKUP_REQUISITOS
```

---

## Outros backups

### Ver código de 29/04 (mais antigo no Git)

```powershell
git checkout backup/ultimo-commit-git
```

### Voltar ao estado **com todas** as melhorias recentes

```powershell
git checkout backup/versao-completa-2026-05-19
```

### Versão **sem** UI de requisitos e **sem** precisão IA extra

```powershell
git checkout backup/antes-precisao-ia
```

### Voltar à branch principal

```powershell
git checkout main
```

---

## Resumo das diferenças

- `backup/ultimo-commit-git` → projeto base de abril, sem requisitos na API
- `backup/antes-precisao-ia` → motor híbrido e integração; API de requisitos no backend, **sem** formulário no frontend
- **`backup/requisitos-vaga-2026-05-19`** → igual à anterior **+** formulário e resultados de requisitos básicos/desejáveis (**só isto a mais**)
- `backup/versao-completa-2026-05-19` → estado completo (inclui precisão IA)

---

## Dados do banco (SQLite / Chroma)

Os backups Git **não incluem** automaticamente:

- `backend/armazenamento/banco_talentos.db`
- `backend/armazenamento/vectordb/`
- `backend/curriculos_arquivos/`

Se precisar de backup desses dados, copie manualmente a pasta `backend/armazenamento/` e `backend/curriculos_arquivos/`.
