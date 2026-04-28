import { useCallback, useEffect, useState } from 'react'
import { MENSAGEM_CARREGANDO, URL_BASE_API } from './constantesApi'
import './App.css'

type CurriculoLista = {
  id: string
  nome_candidato: string
  email: string | null
  nome_arquivo: string
  criado_em: string
  trecho_vista_previa: string | null
}

type ResultadoAnalise = {
  id_candidato: string
  nome_candidato: string
  email: string | null
  nome_arquivo_original: string
  pontuacao_afinidade: number
  score_0_100?: number | null
  justificativa?: string | null
  comentario_padrao: string
}

type AnaliseResposta = {
  mensagem_status: string
  total_antes_corte: number
  resultados: ResultadoAnalise[]
}

type InfoSistema = {
  nome_sistema: string
  descricao: string
  corte_pontuacao: number
  modelo: string
  modelo_reclassificador?: string
  modelo_analise_vaga?: string
  motor_classificacao?: string
  chave_gemini_configurada?: boolean
}

const caminho = (c: string) => `${URL_BASE_API}/api${c}`

function App() {
  const [nomeCandidato, setNomeCandidato] = useState('')
  const [emailCandidato, setEmailCandidato] = useState('')
  const [ficheiroPdf, setFicheiroPdf] = useState<File | null>(null)
  const [lista, setLista] = useState<CurriculoLista[]>([])
  const [aEnviar, setAEnviar] = useState(false)
  const [aCarregar, setACarregar] = useState(false)
  const [aAnalisar, setAAnalisar] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const [descricaoVaga, setDescricaoVaga] = useState('')
  const [quantidadeSugestoes, setQuantidadeSugestoes] = useState(5)
  const [analise, setAnalise] = useState<AnaliseResposta | null>(null)
  const [info, setInfo] = useState<InfoSistema | null>(null)

  const recarregarLista = useCallback(async () => {
    setACarregar(true)
    setErro(null)
    try {
      const r = await fetch(caminho('/curriculos'))
      if (!r.ok) throw new Error(await r.text())
      setLista((await r.json()) as CurriculoLista[])
    } catch (e) {
      setErro(
        e instanceof Error ? e.message : 'Falha ao listar. Confirme se o API está a correr.',
      )
    } finally {
      setACarregar(false)
    }
  }, [])

  const carregarInfo = useCallback(async () => {
    try {
      const r = await fetch(caminho('/sistema/informacoes'))
      if (r.ok) setInfo((await r.json()) as InfoSistema)
    } catch {
      // ignora: backend pode não estar a correr
    }
  }, [])

  useEffect(() => {
    void recarregarLista()
    void carregarInfo()
  }, [recarregarLista, carregarInfo])

  const enviarCurriculo = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (!ficheiroPdf) {
      setErro('Selecione um ficheiro PDF do seu computador.')
      return
    }
    if (nomeCandidato.trim().length < 2) {
      setErro('Preencha o nome do candidato (mínimo 2 carateres).')
      return
    }
    setAEnviar(true)
    setErro(null)
    const fd = new FormData()
    fd.append('candidato', nomeCandidato.trim())
    if (emailCandidato.trim()) fd.append('email', emailCandidato.trim())
    fd.append('arquivo', ficheiroPdf, ficheiroPdf.name)
    try {
      const r = await fetch(caminho('/curriculos/enviar'), { method: 'POST', body: fd })
      if (!r.ok) {
        const d = (await r.json().catch(() => ({}))) as { detail?: unknown }
        const msg =
          typeof d.detail === 'string'
            ? d.detail
            : 'Não foi possível enviar. Tente de novo com outro PDF.'
        throw new Error(msg)
      }
      setFicheiroPdf(null)
      setNomeCandidato('')
      setEmailCandidato('')
      await recarregarLista()
    } catch (e) {
      setErro(e instanceof Error ? e.message : 'Falha no envio')
    } finally {
      setAEnviar(false)
    }
  }

  const excluir = async (id: string) => {
    if (!window.confirm('Remover este registo e o ficheiro associado?')) return
    setErro(null)
    const r = await fetch(caminho(`/curriculos/${encodeURIComponent(id)}`), {
      method: 'DELETE',
    })
    if (!r.ok) {
      setErro('Falha ao excluir.')
      return
    }
    if (analise) setAnalise({ ...analise, resultados: analise.resultados.filter((x) => x.id_candidato !== id) })
    await recarregarLista()
  }

  const analisarVaga = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (descricaoVaga.trim().length < 10) {
      setErro('Descreva a vaga com pelo menos 10 carateres (título, requisitos, stack).')
      return
    }
    setAAnalisar(true)
    setErro(null)
    try {
      const r = await fetch(caminho('/vaga/analise'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          descricao_da_vaga: descricaoVaga.trim(),
          quantidade_sugerida: Math.min(30, Math.max(1, quantidadeSugestoes)),
        }),
      })
      if (!r.ok) throw new Error('Análise indisponível. O gestor de IA pode estar a carregar ainda o modelo.')
      setAnalise((await r.json()) as AnaliseResposta)
    } catch (e) {
      setErro(e instanceof Error ? e.message : 'Falha na análise')
    } finally {
      setAAnalisar(false)
    }
  }

  return (
    <div className="layout">
      <header className="cabeçalho">
        <h1 className="titulo-primario">Análise de currículos com IA</h1>
        <p className="subtitulo">
          {info?.descricao ||
            'Indexação de PDFs, base vetorial e ranking por afinidade com a vaga (PT-BR).'}
        </p>
        {info && (
          <p className="metadados-cabecalho">
            Classificação: {info.motor_classificacao ?? '—'} (
            {info.modelo_analise_vaga ?? '—'}
            {info.chave_gemini_configurada === false ? ' — defina a chave no .env do API' : ''}
            ) · Embeddings: {info.modelo} · Corte: {info.corte_pontuacao} · {info.nome_sistema}
          </p>
        )}
      </header>

      {erro && (
        <div className="alerta alerta--erro" role="alert">
          {erro}
        </div>
      )}

      <div className="grelha">
        <section className="cartao" aria-labelledby="titulo-indexar">
          <h2 id="titulo-indexar">1. Banco de talentos — anexar currículos (PDF)</h2>
          <p className="explicacao">
            Faça o upload a partir do seu computador. O texto do PDF é extraído, guardado e usado
            na busca semântica. Formato suportado: <strong>PDF</strong> apenas.
          </p>
          <form onSubmit={enviarCurriculo} className="formulario">
            <label className="rotulo" htmlFor="cmp-nome">Nome do candidato</label>
            <input
              id="cmp-nome"
              className="entrada"
              value={nomeCandidato}
              onChange={(e) => setNomeCandidato(e.target.value)}
              autoComplete="name"
              required
            />
            <label className="rotulo" htmlFor="cmp-mail">E-mail (opcional)</label>
            <input
              id="cmp-mail"
              type="email"
              className="entrada"
              value={emailCandidato}
              onChange={(e) => setEmailCandidato(e.target.value)}
            />
            <label className="rotulo" htmlFor="cmp-pdf">Ficheiro (PDF)</label>
            <input
              id="cmp-pdf"
              className="entrada entrada--ficheiro"
              type="file"
              accept="application/pdf"
              onChange={(e) => setFicheiroPdf(e.target.files?.[0] ?? null)}
            />
            <button className="botao botao--primario" type="submit" disabled={aEnviar}>
              {aEnviar ? MENSAGEM_CARREGANDO : 'Enviar e indexar no banco'}
            </button>
          </form>
        </section>

        <section className="cartao" aria-labelledby="titulo-vaga">
          <h2 id="titulo-vaga">2. Vaga em aberto — encontrar o melhor alinhamento</h2>
          <p className="explicacao">
            Coloque título, responsabilidades, requisitos desejáveis, linguagens, ferramentas e
            nível. A aplicação percorre o banco e devolve os currículos com maior <em>aderência
            estatística</em> ao perfil, coerente com a política de corte mínima (baixa taxa de
            falsos destaques).
          </p>
          <form onSubmit={analisarVaga} className="formulario">
            <label className="rotulo" htmlFor="vaga">Descrição da vaga (texto livre)</label>
            <textarea
              id="vaga"
              className="texto-grande"
              value={descricaoVaga}
              onChange={(e) => setDescricaoVaga(e.target.value)}
              minLength={10}
              rows={10}
              required
            />
            <div className="fila-rotulos">
              <label className="rotulo" htmlFor="n-sugestoes">Quantos candidatos sugerir (1–30)</label>
            </div>
            <input
              id="n-sugestoes"
              className="entrada entrada--curto"
              type="number"
              min={1}
              max={30}
              value={quantidadeSugestoes}
              onChange={(e) => setQuantidadeSugestoes(Number(e.target.value))}
            />
            <button
              className="botao botao--secundario"
              type="submit"
              disabled={aAnalisar}
            >
              {aAnalisar ? MENSAGEM_CARREGANDO : 'Analisar banco e ordenar por afinidade'}
            </button>
          </form>
        </section>
      </div>

      {analise && (
        <section className="cartao cartao--largo" aria-live="polite">
          <h2>Resultados</h2>
          <p className="explicacao">{analise.mensagem_status}</p>
          {analise.resultados.length === 0 ? (
            <p className="sem-resultados">Sem candidatos acima do corte, ou ainda sem currículos indexados.</p>
          ) : (
            <ol className="ranking">
              {analise.resultados.map((x, i) => (
                <li key={x.id_candidato} className="item-ranking">
                  <span className="pos">#{i + 1}</span>
                  <div>
                    <strong>{x.nome_candidato}</strong>
                    <div className="ficha">
                      {x.email && <span>E-mail: {x.email}</span>}
                      <span>Ficheiro: {x.nome_arquivo_original}</span>
                      <span>ID: {x.id_candidato}</span>
                    </div>
                    <div className="pontuacao-linha">
                      Afinidade (0–1): <strong>{(x.pontuacao_afinidade * 100).toFixed(1)}%</strong>
                      {x.score_0_100 != null && x.score_0_100 !== undefined ? (
                        <span> · Score Gemini: {x.score_0_100}/100</span>
                      ) : null}
                    </div>
                    {x.justificativa ? (
                      <p className="dica-escore justificativa">{x.justificativa}</p>
                    ) : null}
                    <p className="dica-escore">{x.comentario_padrao}</p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      <section className="cartao cartao--largo" aria-labelledby="titulo-idx">
        <h2 id="titulo-idx">Currículos no banco</h2>
        {aCarregar ? <p className="explicacao">{MENSAGEM_CARREGANDO}</p> : null}
        <ul className="lista-idx">
          {lista.length === 0 && !aCarregar ? (
            <li className="sem-idx">Nenhum ficheiro indexado ainda.</li>
          ) : (
            lista.map((c) => (
              <li key={c.id} className="linha-idx">
                <div>
                  <div className="linha-idx--tit">
                    {c.nome_candidato} <span className="nome-arq">({c.nome_arquivo})</span>
                  </div>
                  {c.trecho_vista_previa && (
                    <div className="vista_previa" title="Prévia do texto extraído do PDF">
                      {c.trecho_vista_previa.slice(0, 400)}
                      {c.trecho_vista_previa.length > 400 ? '…' : null}
                    </div>
                  )}
                </div>
                <div className="alinhado-direita">
                  <button
                    className="botao botao--mudo"
                    type="button"
                    onClick={() => excluir(c.id)}
                    aria-label="Excluir"
                  >
                    Excluir
                  </button>
                </div>
              </li>
            ))
          )}
        </ul>
        <p className="refr">
          <button className="botao botao--mudo" type="button" onClick={() => recarregarLista()}>
            Atualizar lista
          </button>
        </p>
      </section>
    </div>
  )
}

export default App
