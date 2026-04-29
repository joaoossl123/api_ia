# Backend - API de Analise de Curriculos

Este backend expõe uma API em FastAPI para:
- cadastrar currículos PDF em um banco de talentos;
- analisar compatibilidade de currículo com vagas;
- ranquear currículos para uma vaga (com Gemini quando disponível, ou fallback local).

## Stack

- Python 3.11
- FastAPI + Uvicorn
- SQLAlchemy (SQLite)
- spaCy, Sentence-Transformers, Transformers
- ChromaDB
- Google Gemini (opcional)

## Estrutura relevante

- `app/main.py`: rotas e bootstrap da API
- `app/configuracao.py`: configuração por variáveis de ambiente
- `app/banco_dados.py`: inicialização e sessão do banco
- `app/servicos/`: pipeline IA, PDF, reclassificação
- `requirements.txt`: dependências Python

## Pre-requisitos

- Python 3.11 instalado
- pip atualizado
- (Opcional) chave Gemini para classificação por IA externa

## Configuração do ambiente

Na pasta `backend`, crie um arquivo `.env` (opcional, mas recomendado):

```env
HOSPEDEIRO_API=127.0.0.1
PORTA_API=8000
ORIGEM_PERMITIDA_FRONTEND=http://127.0.0.1:5173,http://localhost:5173

# Opcional: habilita Gemini
CHAVE_API_GEMINI=
# ou GEMINI_API_KEY=
# ou GOOGLE_API_KEY=
```

Observação: sem chave Gemini, o projeto usa o motor local de reclassificação automaticamente.

## Como executar o backend (desenvolvimento)

1. Entrar na pasta:
```powershell
cd backend
```

2. Instalar dependências:
```powershell
python -m pip install -r requirements.txt
```

3. Instalar modelo do spaCy:
```powershell
python -m spacy download pt_core_news_sm
```

4. Subir a API:
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## URLs de desenvolvimento

- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- Healthcheck: `http://127.0.0.1:8000/saude`

## Endpoints principais

Base prefixada em `/api`:

- `GET /saude`  
  Verifica se o serviço está operante.

- `GET /api/sistema/informacoes`  
  Retorna informações do sistema/modelos e estado da chave Gemini.

- `POST /api/curriculos/enviar`  
  Upload de currículo PDF para o banco de talentos.  
  Form-data:
  - `candidato` (obrigatório)
  - `email` (opcional)
  - `arquivo` (PDF obrigatório)

- `GET /api/curriculos`  
  Lista currículos cadastrados.

- `DELETE /api/curriculos/{id_candidato}`  
  Remove currículo do banco, índice vetorial e arquivo.

- `POST /api/vaga/analise`  
  Analisa descrição de vaga e retorna candidatos ranqueados.

- `POST /api/curriculo/compatibilidade`  
  Analisa um texto de currículo contra vagas informadas no payload.

- `POST /api/curriculo/compatibilidade-pdf`  
  Mesmo tipo de análise, mas enviando um PDF.

## Como executar testes manuais (fluxo recomendado)

Com a API rodando:

1. Healthcheck:
```powershell
curl http://127.0.0.1:8000/saude
```

2. Consultar informações do sistema:
```powershell
curl http://127.0.0.1:8000/api/sistema/informacoes
```

3. Enviar um currículo PDF:
```powershell
curl -X POST "http://127.0.0.1:8000/api/curriculos/enviar" `
  -F "candidato=Maria Silva" `
  -F "email=maria@email.com" `
  -F "arquivo=@C:\caminho\curriculo.pdf"
```

4. Listar currículos cadastrados:
```powershell
curl http://127.0.0.1:8000/api/curriculos
```

5. Testar análise de vaga:
```powershell
curl -X POST "http://127.0.0.1:8000/api/vaga/analise" `
  -H "Content-Type: application/json" `
  -d "{\"descricao_da_vaga\":\"Vaga para desenvolvedora backend Python com FastAPI e SQL.\",\"quantidade_sugerida\":5}"
```

6. (Opcional) Excluir currículo por ID:
```powershell
curl -X DELETE "http://127.0.0.1:8000/api/curriculos/SEU_ID_AQUI"
```

## Observações para o time

- O banco SQLite e o índice vetorial são criados automaticamente no startup.
- Primeiras execuções podem ser mais lentas por download/carga de modelos.
- Sem internet/chave Gemini, o modo local continua funcional para testes.
- Em Windows, instalação de dependências muito grandes pode exigir habilitar suporte a Long Paths.

